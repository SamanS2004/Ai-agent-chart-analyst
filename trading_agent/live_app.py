"""Local live web app: a candlestick chart with FVG/order-block zones and
the current trade plan, updating in your browser in real time.

This has to run somewhere with real network access to your chosen data
source (see README). Once running, open http://<host>:<port> -- the page
itself is served from your own machine, so it isn't subject to any
third-party sandbox's content restrictions; it just needs a browser.

Architecture: a background thread keeps a shared AppState up to date
(seeded once, then a price feed updates it tick by tick and a periodic
full recompute keeps zones/trade-idea current) and pushes each change to
connected browsers over Server-Sent Events via a small pub/sub
Broadcaster. The HTTP server itself is stdlib-only, matching tv_webhook.py.
"""

from __future__ import annotations

import json
import queue
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Literal

from .agent import TradingAgent
from .alerts import ZoneAlertEngine, filter_zones_near_price, zone_specs_from_detections
from .live_stream import BybitTickerWebSocket
from .models import AnalysisResult


class Broadcaster:
    """Thread-safe pub/sub: publish() from any thread, each subscriber gets
    its own queue. A slow/gone client just misses events rather than
    blocking the publisher."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue] = set()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, event: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass  # slow client -- drop this event for them rather than block everyone


class AppState:
    """The current chart state, safe to read (snapshot) and write
    (update_from_analysis / apply_tick) from different threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.symbol = ""
        self.interval = 15
        self.candles: list[dict] = []
        self.fair_value_gaps: list[dict] = []
        self.order_blocks: list[dict] = []
        self.trade_idea: dict | None = None
        self.last_price: float = 0.0

    def update_from_analysis(self, result: AnalysisResult, candles) -> None:
        with self._lock:
            self.symbol = result.symbol
            self.interval = result.interval
            self.candles = [asdict(c) for c in candles]
            self.fair_value_gaps = [asdict(f) for f in result.fair_value_gaps]
            self.order_blocks = [asdict(o) for o in result.order_blocks]
            self.trade_idea = asdict(result.trade_idea) if result.trade_idea else None
            self.last_price = result.last_price

    def apply_tick(self, price: float) -> None:
        """Update the live price and let the still-forming last candle grow
        with it -- this is what makes the chart actually look alive between
        full recomputes. Reconciled properly on the next recompute anyway."""
        with self._lock:
            self.last_price = price
            if self.candles:
                last = self.candles[-1]
                last["close"] = price
                last["high"] = max(last["high"], price)
                last["low"] = min(last["low"], price)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "symbol": self.symbol,
                "interval": self.interval,
                "candles": list(self.candles),
                "fair_value_gaps": list(self.fair_value_gaps),
                "order_blocks": list(self.order_blocks),
                "trade_idea": self.trade_idea,
                "last_price": self.last_price,
            }


def run_live_app(
    agent: TradingAgent,
    broadcaster: Broadcaster,
    state: AppState,
    price_source: Literal["ws", "rest"] = "ws",
    recompute_seconds: int = 60,
    price_poll_seconds: int = 3,
    proximity_pct: float = 0.05,
    stop_event: threading.Event | None = None,
) -> None:
    """Seed `state` synchronously, then start background threads that keep
    it (and connected browsers, via `broadcaster`) updated. Returns once
    seeded -- does not block."""
    stop_event = stop_event or threading.Event()
    engine = ZoneAlertEngine()

    def refresh() -> None:
        candles, fvgs, order_blocks, zones, idea = agent.fetch_and_detect()
        last = candles[-1]
        result = AnalysisResult(
            symbol=agent.symbol,
            interval=agent.interval,
            timestamp_ms=last.timestamp_ms,
            last_price=last.close,
            fair_value_gaps=fvgs,
            order_blocks=order_blocks,
            confluence_zones=zones,
            trade_idea=idea,
        )
        agent.journal.log_analysis(result)
        state.update_from_analysis(result, candles)

        specs = zone_specs_from_detections(fvgs, order_blocks)
        specs = filter_zones_near_price(specs, last.close, proximity_pct)
        engine.sync_zones(specs)

        broadcaster.publish({"type": "zones", **state.snapshot()})

    refresh()  # seed before returning, so the first page load has real data

    def handle_price(price: float, timestamp_ms: int | None = None) -> None:
        state.apply_tick(price)
        broadcaster.publish({"type": "price", "price": price, "timestamp_ms": timestamp_ms})
        for alert in engine.on_price(price, timestamp_ms):
            agent.journal.log_alert(alert)
            broadcaster.publish({"type": "alert", **asdict(alert)})

    def recompute_loop() -> None:
        while not stop_event.wait(recompute_seconds):
            try:
                refresh()
            except Exception as exc:  # keep the app up even if one refresh fails
                agent.journal.log_event(f"live app refresh error: {exc}")

    threading.Thread(target=recompute_loop, daemon=True).start()

    if price_source == "ws":
        stream = BybitTickerWebSocket(
            symbol=agent.client.symbol,
            on_price=handle_price,
            on_status=lambda msg: agent.journal.log_event(f"price stream: {msg}"),
        )
        stream.start()  # non-blocking, runs in its own daemon thread
    else:

        def rest_loop() -> None:
            while not stop_event.wait(price_poll_seconds):
                try:
                    handle_price(agent.client.get_ticker_price())
                except Exception as exc:
                    agent.journal.log_event(f"price poll error: {exc}")

        threading.Thread(target=rest_loop, daemon=True).start()


class _ChartHTTPServer(ThreadingHTTPServer):
    def __init__(self, address, handler_cls, state: AppState, broadcaster: Broadcaster) -> None:
        super().__init__(address, handler_cls)
        self.state = state
        self.broadcaster = broadcaster


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._serve_html()
        elif self.path == "/state":
            self._serve_state()
        elif self.path == "/events":
            self._serve_events()
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_html(self) -> None:
        body = PAGE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_state(self) -> None:
        body = json.dumps(self.server.state.snapshot()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = self.server.broadcaster.subscribe()
        try:
            while True:
                try:
                    event = q.get(timeout=15)
                    chunk = f"data: {json.dumps(event)}\n\n".encode("utf-8")
                except queue.Empty:
                    chunk = b": keep-alive\n\n"
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.server.broadcaster.unsubscribe(q)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass  # keep stdout to the CLI's own status line


def serve(
    agent: TradingAgent,
    host: str = "127.0.0.1",
    port: int = 8080,
    price_source: Literal["ws", "rest"] = "ws",
    recompute_seconds: int = 60,
    price_poll_seconds: int = 3,
    proximity_pct: float = 0.05,
    on_ready: Callable[[], None] | None = None,
) -> None:
    """`on_ready` fires only after the initial data fetch succeeds and the
    HTTP server is about to start listening -- so a caller's "open your
    browser now" message can't print ahead of a startup failure (e.g. no
    network access to the data source) and end up misleading the user."""
    state = AppState()
    broadcaster = Broadcaster()
    run_live_app(
        agent,
        broadcaster,
        state,
        price_source=price_source,
        recompute_seconds=recompute_seconds,
        price_poll_seconds=price_poll_seconds,
        proximity_pct=proximity_pct,
    )
    server = _ChartHTTPServer((host, port), _Handler, state, broadcaster)
    if on_ready is not None:
        on_ready()
    try:
        server.serve_forever()
    finally:
        server.server_close()


PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>BTC Zone Chart</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    color-scheme: dark;
    --surface: #0D1117; --surface-raised: #141A22; --surface-sunken: #0A0E13;
    --border: #232B36; --ink: #E9E7E2; --ink-muted: #8B94A3; --ink-faint: #5B6472;
    --accent: #E8A33D; --accent-ink: #1A1206;
    --good-line: #22c55e; --critical-line: #ef4444;
    --good-bg: rgba(34,197,94,0.14); --critical-bg: rgba(239,68,68,0.14);
    --fvg-bull: rgba(34,197,94,0.16); --fvg-bear: rgba(239,68,68,0.16);
    --ob-bull: rgba(34,197,94,0.10); --ob-bear: rgba(239,68,68,0.10);
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px 16px 40px; background: var(--surface); color: var(--ink);
         font-family: "IBM Plex Sans", -apple-system, BlinkMacSystemFont, sans-serif; font-size: 14px; }
  .mono { font-family: "IBM Plex Mono", ui-monospace, monospace; font-variant-numeric: tabular-nums; }
  .wrap { max-width: 1080px; margin: 0 auto; display: flex; flex-direction: column; gap: 16px; }
  .masthead { background: var(--surface-raised); border: 1px solid var(--border); border-radius: 8px;
              padding: 16px 20px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 14px; }
  .masthead-id { display: flex; flex-direction: column; gap: 6px; }
  .eyebrow { font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: var(--ink-faint); font-weight: 600; }
  .symbol-row { display: flex; align-items: baseline; gap: 10px; }
  .symbol { font-size: 21px; font-weight: 700; }
  .tf-chip { font-size: 11px; font-weight: 600; color: var(--accent-ink); background: var(--accent); padding: 2px 7px; border-radius: 4px; font-family: "IBM Plex Mono", monospace; }
  .conn-note { font-size: 12px; color: var(--ink-muted); display: flex; align-items: center; gap: 7px; }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ink-faint); flex: none; transition: background .2s; }
  .dot.live { background: var(--good-line); box-shadow: 0 0 0 3px var(--good-bg); }
  .price-block { text-align: right; display: flex; flex-direction: column; gap: 3px; }
  .price-label { font-size: 11px; letter-spacing: .06em; text-transform: uppercase; color: var(--ink-faint); }
  .price-value { font-size: 26px; font-weight: 500; transition: color .3s; }
  .price-value.up { color: var(--good-line); } .price-value.down { color: var(--critical-line); }
  section { background: var(--surface-raised); border: 1px solid var(--border); border-radius: 8px; padding: 16px 16px 6px; }
  section h2 { margin: 0 0 4px; font-size: 13px; font-weight: 600; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
  .legend { display: flex; flex-wrap: wrap; gap: 12px; font-size: 11.5px; color: var(--ink-muted); margin-bottom: 10px; }
  .legend span { display: inline-flex; align-items: center; gap: 5px; }
  .swatch { width: 11px; height: 11px; border-radius: 2px; display: inline-block; }
  .swatch.line { width: 16px; height: 0; border-top: 2px dashed currentColor; border-radius: 0; }
  .chart-wrap { position: relative; }
  #chart { width: 100%; height: auto; display: block; overflow: visible; }
  .axis-label { fill: var(--ink-faint); font-size: 10px; font-family: "IBM Plex Mono", monospace; }
  .zone-label { font-size: 9.5px; font-family: "IBM Plex Mono", monospace; font-weight: 600; }
  .plan-label { font-size: 10.5px; font-family: "IBM Plex Mono", monospace; font-weight: 600; }
  .gridline { stroke: var(--border); stroke-width: 1; }
  .tooltip { position: absolute; pointer-events: none; background: var(--surface-sunken); border: 1px solid var(--border);
             border-radius: 6px; padding: 8px 10px; font-size: 11.5px; white-space: nowrap; opacity: 0; transition: opacity .08s; z-index: 3; }
  .tooltip .row { display: flex; justify-content: space-between; gap: 14px; }
  .tooltip .t { color: var(--ink-faint); font-size: 10px; margin-bottom: 4px; }
  .rr-callout { display: flex; flex-wrap: wrap; gap: 18px; align-items: center; background: var(--surface-sunken);
                border: 1px solid var(--border); border-radius: 8px; padding: 14px 18px; margin: 12px 0 4px; }
  .rr-item .k { font-size: 10.5px; text-transform: uppercase; letter-spacing: .05em; color: var(--ink-faint); }
  .rr-item .v { font-size: 16px; font-weight: 600; }
  .rr-badge { margin-left: auto; font-size: 13px; font-weight: 700; padding: 6px 14px; border-radius: 999px;
              background: var(--good-bg); color: var(--good-line); border: 1px solid var(--good-line); }
  .rationale { font-size: 12.5px; color: var(--ink-muted); padding: 2px 4px 12px; }
  #alertFeed { display: flex; flex-direction: column; gap: 6px; padding: 4px 4px 14px; max-height: 220px; overflow-y: auto; }
  .alert-row { display: flex; gap: 10px; font-size: 12px; align-items: baseline; }
  .alert-row .ev { font-weight: 700; padding: 1px 7px; border-radius: 999px; font-size: 10.5px; }
  .alert-row .ev.touch { color: var(--ink); border: 1px solid var(--border); }
  .alert-row .ev.retest { color: var(--good-line); border: 1px solid var(--good-line); }
  .alert-row .ev.disrespect { color: var(--critical-line); border: 1px solid var(--critical-line); }
  .alert-row .t { color: var(--ink-faint); font-size: 10.5px; }
  .empty { color: var(--ink-faint); font-style: italic; font-size: 12.5px; padding: 4px 4px 14px; }
  .toast { position: fixed; right: 16px; bottom: 16px; background: var(--surface-sunken); border: 1px solid var(--border);
           border-radius: 8px; padding: 10px 14px; font-size: 12.5px; max-width: 320px; box-shadow: 0 8px 24px rgba(0,0,0,.4);
           transform: translateY(12px); opacity: 0; transition: all .25s ease; z-index: 10; }
  .toast.show { transform: translateY(0); opacity: 1; }
  footer { text-align: center; font-size: 11.5px; color: var(--ink-faint); padding: 4px 4px 0; }
  @media (max-width: 640px) { .masthead { flex-direction: column; align-items: flex-start; } .price-block { text-align: left; } .rr-badge { margin-left: 0; } }
</style>
</head>
<body>
<div class="wrap">
  <div class="masthead">
    <div class="masthead-id">
      <span class="eyebrow">FVG &amp; order-block live chart</span>
      <div class="symbol-row"><span class="symbol" id="symbolName">&mdash;</span><span class="tf-chip" id="tfChip">&mdash;</span></div>
      <div class="conn-note"><span class="dot" id="connDot"></span> <span id="connNote">connecting&hellip;</span></div>
    </div>
    <div class="price-block">
      <span class="price-label">Last price</span>
      <span class="price-value mono" id="lastPrice">&mdash;</span>
    </div>
  </div>

  <section>
    <h2><span>Price with FVG / order-block zones and trade plan</span><span id="zoneCount" style="font-weight:400;color:var(--ink-faint);font-size:11.5px;"></span></h2>
    <div class="legend">
      <span><i class="swatch" style="background:var(--fvg-bull);border:1px solid var(--good-line);"></i>Bullish FVG</span>
      <span><i class="swatch" style="background:var(--fvg-bear);border:1px solid var(--critical-line);"></i>Bearish FVG</span>
      <span><i class="swatch" style="background:var(--ob-bull);border:1px dashed var(--good-line);"></i>Bullish order block</span>
      <span><i class="swatch" style="background:var(--ob-bear);border:1px dashed var(--critical-line);"></i>Bearish order block</span>
      <span><i class="swatch line" style="color:var(--accent);"></i>Entry</span>
      <span><i class="swatch line" style="color:var(--critical-line);"></i>Stop</span>
      <span><i class="swatch line" style="color:var(--good-line);"></i>Target</span>
    </div>
    <div class="chart-wrap">
      <svg id="chart" viewBox="0 0 1040 620" role="img" aria-label="Live BTC candlestick chart with FVG and order block zones and the current trade plan"></svg>
      <div class="tooltip" id="tooltip"></div>
    </div>
  </section>

  <section id="planSection"><h2>Trade plan</h2></section>

  <section>
    <h2>Live alert feed <span id="alertCount" style="font-weight:400;color:var(--ink-faint);font-size:11.5px;"></span></h2>
    <div id="alertFeed"><p class="empty">No touches, retests, or disrespects yet this session.</p></div>
  </section>

  <footer>Served locally by <span class="mono">trading-agent app</span> &middot; real detection code, real-time data &middot; not financial advice</footer>
</div>
<div class="toast" id="toast"></div>

<script>
let DATA = { symbol: '', interval: 15, candles: [], fair_value_gaps: [], order_blocks: [], trade_idea: null, last_price: 0 };
let alertLog = [];

const fmt = (n) => (n ?? 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtTime = (ms) => new Date(ms).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
const fmtTimeShort = (ms) => new Date(ms).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });

function setConn(live) {
  document.getElementById('connDot').classList.toggle('live', live);
  document.getElementById('connNote').textContent = live ? 'live' : 'connecting…';
}

function renderMasthead(flashDir) {
  document.getElementById('symbolName').textContent = DATA.symbol || '—';
  document.getElementById('tfChip').textContent = DATA.interval ? DATA.interval + 'm' : '—';
  const el = document.getElementById('lastPrice');
  el.textContent = '$' + fmt(DATA.last_price);
  if (flashDir) {
    el.classList.remove('up', 'down');
    void el.offsetWidth;
    el.classList.add(flashDir);
  }
}

function buildChart() {
  const svg = document.getElementById('chart');
  const W = 1040, H = 620, padL = 66, padR = 148, padT = 20, padB = 34;
  const plotW = W - padL - padR, plotH = H - padT - padB;

  const allCandles = DATA.candles;
  if (allCandles.length < 2) { svg.innerHTML = ''; return; }
  const N = Math.min(45, allCandles.length);
  const offset = allCandles.length - N;
  const candles = allCandles.slice(offset);

  const inView = (z) => z.index >= offset;
  const liveFvgs = DATA.fair_value_gaps.filter((z) => !z.mitigated && inView(z));
  const liveObs = DATA.order_blocks.filter((z) => !z.mitigated && inView(z));
  const idea = DATA.trade_idea;

  const allLiveFvgs = DATA.fair_value_gaps.filter((z) => !z.mitigated).length;
  const allLiveObs = DATA.order_blocks.filter((z) => !z.mitigated).length;
  const offscreen = (allLiveFvgs + allLiveObs) - (liveFvgs.length + liveObs.length);
  document.getElementById('zoneCount').textContent =
    `${liveFvgs.length} FVG / ${liveObs.length} order block unmitigated in view` + (offscreen > 0 ? ` (+${offscreen} further back)` : '');

  let lo = Math.min(...candles.map((c) => c.low));
  let hi = Math.max(...candles.map((c) => c.high));
  [...liveFvgs, ...liveObs].forEach((z) => { lo = Math.min(lo, z.bottom); hi = Math.max(hi, z.top); });
  if (idea) { lo = Math.min(lo, idea.stop, ...idea.targets); hi = Math.max(hi, idea.stop, ...idea.targets); }
  const span = (hi - lo) || 1;
  lo -= span * 0.03; hi += span * 0.03;

  const x = (i) => padL + plotW * (i + 0.5) / N;
  const y = (p) => padT + plotH * (hi - p) / (hi - lo);
  const candleW = Math.max(2, (plotW / N) * 0.62);
  const parts = [];

  for (let k = 0; k <= 4; k++) {
    const price = lo + (span * 1.06) * k / 4 - span * 0.03;
    const yy = y(price).toFixed(1);
    parts.push(`<line class="gridline" x1="${padL}" x2="${padL + plotW}" y1="${yy}" y2="${yy}" />`);
    if (k > 0) parts.push(`<text class="axis-label" x="${padL - 8}" y="${Number(yy) + 3}" text-anchor="end">$${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}</text>`);
  }
  [0, Math.floor(N / 4), Math.floor(N / 2), Math.floor(3 * N / 4), N - 1].forEach((i) => {
    parts.push(`<text class="axis-label" x="${x(i).toFixed(1)}" y="${H - 10}" text-anchor="middle">${fmtTimeShort(candles[i].timestamp_ms)}</text>`);
  });

  const plotRight = padL + plotW;
  function zoneX0(z) { return x(Math.max(0, z.index - offset)) - candleW / 2; }
  function drawZone(z, kind) {
    const fill = kind === 'fvg' ? (z.kind === 'bullish' ? 'var(--fvg-bull)' : 'var(--fvg-bear)') : (z.kind === 'bullish' ? 'var(--ob-bull)' : 'var(--ob-bear)');
    const stroke = z.kind === 'bullish' ? 'var(--good-line)' : 'var(--critical-line)';
    const dash = kind === 'ob' ? ' stroke-dasharray="4 3"' : '';
    const x0 = zoneX0(z), yTop = y(z.top), yBot = y(z.bottom);
    parts.push(`<rect x="${x0.toFixed(1)}" y="${yTop.toFixed(1)}" width="${(plotRight - x0).toFixed(1)}" height="${Math.max(1, yBot - yTop).toFixed(1)}" fill="${fill}" stroke="${stroke}" stroke-width="1"${dash} />`);
    parts.push(`<text class="zone-label" x="${(x0 + 4).toFixed(1)}" y="${(yTop + 10).toFixed(1)}" fill="${stroke}">${kind === 'fvg' ? 'FVG' : 'OB'}</text>`);
  }
  liveFvgs.forEach((z) => drawZone(z, 'fvg'));
  liveObs.forEach((z) => drawZone(z, 'ob'));

  if (idea) {
    const entryY = y(idea.entry), stopY = y(idea.stop), t1Y = y(idea.targets[0]), t2Y = y(idea.targets[1]);
    const riskTop = Math.min(entryY, stopY), riskBot = Math.max(entryY, stopY);
    const rewardTop = Math.min(entryY, t2Y), rewardBot = Math.max(entryY, t2Y);
    parts.push(`<rect x="${padL}" y="${rewardTop.toFixed(1)}" width="${plotW}" height="${(rewardBot - rewardTop).toFixed(1)}" fill="var(--good-bg)" />`);
    parts.push(`<rect x="${padL}" y="${riskTop.toFixed(1)}" width="${plotW}" height="${(riskBot - riskTop).toFixed(1)}" fill="var(--critical-bg)" />`);
    const line = (yy, color, width, dash) => `<line x1="${padL}" x2="${plotRight}" y1="${yy.toFixed(1)}" y2="${yy.toFixed(1)}" stroke="${color}" stroke-width="${width}"${dash ? ` stroke-dasharray="${dash}"` : ''} />`;
    const label = (yy, text, color, bold) => `<text class="plan-label" x="${plotRight + 8}" y="${(yy + 3.5).toFixed(1)}" fill="${color}" ${bold ? 'font-size="11.5"' : ''}>${text}</text>`;
    parts.push(line(entryY, 'var(--accent)', 1.6, '5 3')); parts.push(label(entryY, `ENTRY $${fmt(idea.entry)}`, 'var(--accent)'));
    parts.push(line(stopY, 'var(--critical-line)', 1.8)); parts.push(label(stopY, `STOP $${fmt(idea.stop)}`, 'var(--critical-line)'));
    parts.push(line(t1Y, 'var(--good-line)', 1.2, '3 3')); parts.push(label(t1Y, `T1 (2R) $${fmt(idea.targets[0])}`, 'var(--good-line)'));
    parts.push(line(t2Y, 'var(--good-line)', 2.4)); parts.push(label(t2Y, `T2 (3R) $${fmt(idea.targets[1])}`, 'var(--good-line)', true));
  }

  candles.forEach((c, i) => {
    const up = c.close >= c.open;
    const color = up ? 'var(--good-line)' : 'var(--critical-line)';
    const xc = x(i).toFixed(1);
    parts.push(`<line x1="${xc}" x2="${xc}" y1="${y(c.high).toFixed(1)}" y2="${y(c.low).toFixed(1)}" stroke="${color}" stroke-width="1.1" />`);
    const bodyTop = y(Math.max(c.open, c.close)), bodyBot = y(Math.min(c.open, c.close));
    parts.push(`<rect x="${(x(i) - candleW / 2).toFixed(1)}" y="${bodyTop.toFixed(1)}" width="${candleW.toFixed(1)}" height="${Math.max(1, bodyBot - bodyTop).toFixed(1)}" fill="${color}" />`);
  });

  const lastY = y(candles[candles.length - 1].close);
  parts.push(`<line x1="${padL}" x2="${plotRight}" y1="${lastY.toFixed(1)}" y2="${lastY.toFixed(1)}" stroke="var(--ink-faint)" stroke-width="1" stroke-dasharray="2 3" opacity="0.6" />`);

  parts.push(`<line id="crosshair" x1="0" x2="0" y1="${padT}" y2="${padT + plotH}" stroke="var(--ink-faint)" stroke-width="1" stroke-dasharray="3 3" opacity="0" />`);
  parts.push(`<rect id="hoverCatcher" x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="transparent" />`);

  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.innerHTML = parts.join('\n');

  const catcher = document.getElementById('hoverCatcher');
  const crosshair = document.getElementById('crosshair');
  const tooltip = document.getElementById('tooltip');
  catcher.addEventListener('mousemove', (e) => {
    const rect = svg.getBoundingClientRect();
    const mouseX = ((e.clientX - rect.left) / rect.width) * W;
    let i = Math.round((mouseX - padL) / (plotW / N) - 0.5);
    i = Math.max(0, Math.min(N - 1, i));
    const c = candles[i], px = x(i);
    crosshair.setAttribute('x1', px); crosshair.setAttribute('x2', px); crosshair.setAttribute('opacity', '0.6');
    tooltip.style.opacity = '1';
    tooltip.style.left = ((px / W) * rect.width) + 'px';
    tooltip.style.top = ((y(c.high) / H) * rect.height - 10) + 'px';
    tooltip.style.transform = 'translate(-50%, -100%)';
    tooltip.innerHTML = `<div class="t">${fmtTime(c.timestamp_ms)}</div>
      <div class="row"><span>O</span><span class="mono">${fmt(c.open)}</span></div>
      <div class="row"><span>H</span><span class="mono">${fmt(c.high)}</span></div>
      <div class="row"><span>L</span><span class="mono">${fmt(c.low)}</span></div>
      <div class="row"><span>C</span><span class="mono">${fmt(c.close)}</span></div>`;
  });
  catcher.addEventListener('mouseleave', () => { crosshair.setAttribute('opacity', '0'); tooltip.style.opacity = '0'; });
}

function renderPlan() {
  const idea = DATA.trade_idea;
  const section = document.getElementById('planSection');
  if (!idea) {
    section.innerHTML = '<h2>Trade plan</h2><p class="rationale">No qualifying setup this cycle — price isn’t currently approaching an unmitigated zone.</p>';
    return;
  }
  const risk = Math.abs(idea.entry - idea.stop);
  const reward2 = Math.abs(idea.targets[1] - idea.entry);
  const ratio = (reward2 / risk).toFixed(1);
  section.innerHTML = `<h2>Trade plan</h2>
    <p class="rationale">${idea.rationale}</p>
    <div class="rr-callout">
      <div class="rr-item"><div class="k">Side</div><div class="v" style="color:${idea.kind === 'long' ? 'var(--good-line)' : 'var(--critical-line)'}">${idea.kind.toUpperCase()}</div></div>
      <div class="rr-item"><div class="k">Risk (1R)</div><div class="v mono">$${fmt(risk)}</div></div>
      <div class="rr-item"><div class="k">Reward at T2 (3R)</div><div class="v mono">$${fmt(reward2)}</div></div>
      <div class="rr-item"><div class="k">Confidence</div><div class="v">${idea.confidence}</div></div>
      <div class="rr-badge">${ratio}:1 reward-to-risk</div>
    </div>`;
}

function renderAll(flashDir) {
  renderMasthead(flashDir);
  buildChart();
  renderPlan();
}

function pushAlert(a) {
  alertLog.unshift(a);
  alertLog = alertLog.slice(0, 30);
  document.getElementById('alertCount').textContent = alertLog.length ? `${alertLog.length} this session` : '';
  document.getElementById('alertFeed').innerHTML = alertLog.map((a) =>
    `<div class="alert-row"><span class="t">${fmtTimeShort(a.timestamp_ms)}</span><span class="ev ${a.event}">${a.event}</span><span>${a.message}</span></div>`
  ).join('');
}

function showToast(text) {
  const t = document.getElementById('toast');
  t.textContent = text;
  t.classList.add('show');
  clearTimeout(showToast._h);
  showToast._h = setTimeout(() => t.classList.remove('show'), 4000);
}

async function boot() {
  const res = await fetch('/state');
  DATA = await res.json();
  renderAll(null);

  const es = new EventSource('/events');
  es.onopen = () => setConn(true);
  es.onerror = () => setConn(false);
  es.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === 'price') {
      const dir = msg.price > DATA.last_price ? 'up' : (msg.price < DATA.last_price ? 'down' : null);
      DATA.last_price = msg.price;
      if (DATA.candles.length) {
        const last = DATA.candles[DATA.candles.length - 1];
        last.close = msg.price;
        last.high = Math.max(last.high, msg.price);
        last.low = Math.min(last.low, msg.price);
      }
      renderAll(dir);
    } else if (msg.type === 'zones') {
      DATA = msg;
      renderAll(null);
    } else if (msg.type === 'alert') {
      pushAlert(msg);
      showToast(msg.message);
    }
  };
}
boot();
</script>
</body>
</html>
"""
