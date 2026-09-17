"""Local live web dashboard: a fast-to-scan table of every tracked pair
(gain %, volume multiplier, drawdown from peak) plus a live entry/exit
alert feed, updating in your browser in real time.

The whole point is to make timing legible at a glance -- memecoin moves
happen in minutes, so this isn't a single-symbol chart like the BTC agent's
live_app.py, it's a sortable-by-eye table across everything currently being
tracked, with color coding standing in for "is this an entry, is this an
exit, is this just noise." It never places trades or touches a wallet --
each row links out to the pair's DexScreener page (many of which have their
own buy/swap widget) so execution stays in your own wallet, on your own
terms.

Architecture matches trading_agent/live_app.py: a background thread keeps a
shared AppState up to date (seeded once, then a poll loop keeps it current)
and pushes each change to connected browsers over Server-Sent Events via a
small pub/sub Broadcaster. The HTTP server itself is stdlib-only.
"""

from __future__ import annotations

import json
import queue
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .agent import SolanaMemecoinAgent
from .models import Alert, TrackedPair


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


MAX_ALERTS_KEPT = 50


def _tracked_to_dict(t: TrackedPair) -> dict:
    p = t.pair
    return {
        "pair_address": p.pair_address,
        "symbol": p.base_symbol,
        "name": p.base_name,
        "dex_id": p.dex_id,
        "chain_id": p.chain_id,
        "price_usd": p.price_usd,
        "liquidity_usd": p.liquidity_usd,
        "volume_m5": p.volume_m5,
        "volume_h24": p.volume_h24,
        "gain_pct": t.gain_pct,
        "volume_multiplier": t.volume_multiplier,
        "peak_price": t.peak_price,
        "drawdown_from_peak_pct": t.drawdown_from_peak_pct,
        "first_seen_ms": t.first_seen_ms,
        "last_updated_ms": t.last_updated_ms,
        "pair_created_at_ms": p.pair_created_at_ms,
        "url": p.url,
    }


class AppState:
    """The current dashboard state, safe to read (snapshot) and write
    (set_thresholds / update_pairs / push_alert) from different threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.chain_id = ""
        self.thresholds: dict = {}
        self.pairs: dict[str, dict] = {}
        self.alerts: list[dict] = []

    def set_thresholds(self, chain_id: str, thresholds) -> None:
        with self._lock:
            self.chain_id = chain_id
            self.thresholds = asdict(thresholds)

    def update_pairs(self, tracked_list: list[TrackedPair]) -> None:
        with self._lock:
            current = {t.pair.pair_address for t in tracked_list}
            for address in list(self.pairs.keys()):
                if address not in current:
                    del self.pairs[address]  # no longer discovered -- drop it from the board
            for t in tracked_list:
                self.pairs[t.pair.pair_address] = _tracked_to_dict(t)

    def push_alert(self, alert: Alert) -> None:
        with self._lock:
            self.alerts.insert(0, asdict(alert))
            self.alerts = self.alerts[:MAX_ALERTS_KEPT]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "chain_id": self.chain_id,
                "thresholds": dict(self.thresholds),
                "pairs": list(self.pairs.values()),
                "alerts": list(self.alerts),
            }


def run_live_app(
    agent: SolanaMemecoinAgent,
    broadcaster: Broadcaster,
    state: AppState,
    poll_seconds: int = 30,
    discover_seconds: int = 300,
    stop_event: threading.Event | None = None,
) -> None:
    """Seed `state` synchronously (so a startup failure -- e.g. no network --
    surfaces before the caller announces the app is ready), then start a
    background thread that keeps it (and connected browsers, via
    `broadcaster`) updated. Returns once seeded -- does not block."""
    stop_event = stop_event or threading.Event()
    state.set_thresholds(agent.chain_id, agent.engine.thresholds)

    def on_alert(alert: Alert) -> None:
        state.push_alert(alert)
        broadcaster.publish({"type": "alert", **asdict(alert)})

    def poll_once(addresses: list[str]) -> None:
        tracked_list = agent.poll_and_signal(addresses, on_alert)
        state.update_pairs(tracked_list)
        broadcaster.publish({"type": "pairs", **state.snapshot()})

    addresses = agent.refresh_candidates()
    poll_once(addresses)  # seed before returning, so the first page load has real data

    def loop() -> None:
        nonlocal addresses
        elapsed_since_discover = 0
        while not stop_event.wait(poll_seconds):
            try:
                poll_once(addresses)
            except Exception as exc:  # keep the app up even if one poll fails
                agent.journal.log_event(f"live app poll error: {exc}")
            elapsed_since_discover += poll_seconds
            if elapsed_since_discover >= discover_seconds:
                try:
                    addresses = agent.refresh_candidates()
                except Exception as exc:
                    agent.journal.log_event(f"live app discovery error: {exc}")
                elapsed_since_discover = 0

    threading.Thread(target=loop, daemon=True).start()


class _DashboardHTTPServer(ThreadingHTTPServer):
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
    agent: SolanaMemecoinAgent,
    host: str = "127.0.0.1",
    port: int = 8090,
    poll_seconds: int = 30,
    discover_seconds: int = 300,
    on_ready: Callable[[], None] | None = None,
) -> None:
    """`on_ready` fires only after the initial poll succeeds and the HTTP
    server is about to start listening -- so a caller's "open your browser
    now" message can't print ahead of a startup failure (e.g. no network
    access to DexScreener) and end up misleading the user."""
    state = AppState()
    broadcaster = Broadcaster()
    run_live_app(agent, broadcaster, state, poll_seconds=poll_seconds, discover_seconds=discover_seconds)
    server = _DashboardHTTPServer((host, port), _Handler, state, broadcaster)
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
<title>Memecoin Tracker</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    color-scheme: dark;
    --surface: #0D1117; --surface-raised: #141A22; --surface-sunken: #0A0E13;
    --border: #232B36; --ink: #E9E7E2; --ink-muted: #8B94A3; --ink-faint: #5B6472;
    --accent: #E8A33D; --accent-ink: #1A1206;
    --good-line: #22c55e; --critical-line: #ef4444; --warn-line: #f59e0b;
    --good-bg: rgba(34,197,94,0.14); --critical-bg: rgba(239,68,68,0.14); --warn-bg: rgba(245,158,11,0.14);
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px 16px 40px; background: var(--surface); color: var(--ink);
         font-family: "IBM Plex Sans", -apple-system, BlinkMacSystemFont, sans-serif; font-size: 14px; }
  .mono { font-family: "IBM Plex Mono", ui-monospace, monospace; font-variant-numeric: tabular-nums; }
  .wrap { max-width: 1180px; margin: 0 auto; display: flex; flex-direction: column; gap: 16px; }
  .masthead { background: var(--surface-raised); border: 1px solid var(--border); border-radius: 8px;
              padding: 16px 20px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 14px; }
  .masthead-id { display: flex; flex-direction: column; gap: 6px; }
  .eyebrow { font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: var(--ink-faint); font-weight: 600; }
  .title-row { display: flex; align-items: baseline; gap: 10px; }
  .title { font-size: 21px; font-weight: 700; }
  .chain-chip { font-size: 11px; font-weight: 600; color: var(--accent-ink); background: var(--accent); padding: 2px 7px; border-radius: 4px; font-family: "IBM Plex Mono", monospace; text-transform: capitalize; }
  .conn-note { font-size: 12px; color: var(--ink-muted); display: flex; align-items: center; gap: 7px; }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ink-faint); flex: none; transition: background .2s; }
  .dot.live { background: var(--good-line); box-shadow: 0 0 0 3px var(--good-bg); }
  .thresholds { text-align: right; font-size: 11.5px; color: var(--ink-muted); line-height: 1.6; }
  .thresholds b { color: var(--ink); }
  section { background: var(--surface-raised); border: 1px solid var(--border); border-radius: 8px; padding: 16px 16px 6px; }
  section h2 { margin: 0 0 4px; font-size: 13px; font-weight: 600; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
  .legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 11.5px; color: var(--ink-muted); margin-bottom: 10px; }
  .legend span { display: inline-flex; align-items: center; gap: 5px; }
  .swatch { width: 11px; height: 11px; border-radius: 2px; display: inline-block; }
  table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  thead th { text-align: right; font-size: 10.5px; text-transform: uppercase; letter-spacing: .04em; color: var(--ink-faint);
             font-weight: 600; padding: 6px 8px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  thead th:first-child, tbody td:first-child { text-align: left; }
  tbody td { padding: 9px 8px; border-bottom: 1px solid var(--border); text-align: right; white-space: nowrap; }
  tbody tr.entry-zone { box-shadow: inset 3px 0 0 var(--good-line); }
  tbody tr.exit-zone { box-shadow: inset 3px 0 0 var(--warn-line); }
  .sym { display: flex; flex-direction: column; gap: 2px; text-align: left; }
  .sym .name { font-weight: 600; }
  .sym .dex { font-size: 10.5px; color: var(--ink-faint); }
  .pct { font-weight: 600; }
  .pct.hot { color: var(--good-line); }
  .pct.cold { color: var(--ink-muted); }
  .badge { display: inline-block; font-size: 9.5px; font-weight: 700; padding: 1px 6px; border-radius: 999px; margin-left: 6px; vertical-align: 1px; }
  .badge.zone { color: var(--good-line); border: 1px solid var(--good-line); }
  .badge.exit { color: var(--warn-line); border: 1px solid var(--warn-line); }
  .drawdown { color: var(--ink-muted); }
  .drawdown.hot { color: var(--warn-line); font-weight: 600; }
  .vmul { color: var(--ink-muted); }
  .vmul.hot { color: var(--accent); font-weight: 600; }
  .chart-link { display: inline-block; font-size: 11px; font-weight: 600; color: var(--accent-ink); background: var(--accent);
                padding: 3px 9px; border-radius: 5px; text-decoration: none; white-space: nowrap; }
  .chart-link:hover { opacity: .85; }
  #alertFeed { display: flex; flex-direction: column; gap: 8px; padding: 4px 4px 14px; max-height: 320px; overflow-y: auto; }
  .alert-card { display: flex; gap: 10px; align-items: center; font-size: 12.5px; padding: 8px 10px; border-radius: 6px; background: var(--surface-sunken); border: 1px solid var(--border); }
  .alert-card .ev { font-weight: 700; padding: 2px 8px; border-radius: 999px; font-size: 10.5px; flex: none; }
  .alert-card .ev.volume_and_gain { color: var(--good-line); border: 1px solid var(--good-line); }
  .alert-card .ev.pullback { color: var(--warn-line); border: 1px solid var(--warn-line); }
  .alert-card .msg { flex: 1; color: var(--ink-muted); }
  .alert-card .msg b { color: var(--ink); }
  .alert-card .t { color: var(--ink-faint); font-size: 10.5px; flex: none; }
  .empty { color: var(--ink-faint); font-style: italic; font-size: 12.5px; padding: 4px 4px 14px; }
  .toast { position: fixed; right: 16px; bottom: 16px; background: var(--surface-sunken); border: 1px solid var(--border);
           border-radius: 8px; padding: 10px 14px; font-size: 12.5px; max-width: 340px; box-shadow: 0 8px 24px rgba(0,0,0,.4);
           transform: translateY(12px); opacity: 0; transition: all .25s ease; z-index: 10; }
  .toast.show { transform: translateY(0); opacity: 1; }
  .toast.exit { border-color: var(--warn-line); }
  .toast.entry { border-color: var(--good-line); }
  footer { text-align: center; font-size: 11.5px; color: var(--ink-faint); padding: 4px 4px 0; }
  @media (max-width: 640px) { .masthead { flex-direction: column; align-items: flex-start; } .thresholds { text-align: left; }
    table { display: block; overflow-x: auto; } }
</style>
</head>
<body>
<div class="wrap">
  <div class="masthead">
    <div class="masthead-id">
      <span class="eyebrow">Memecoin volume/gain tracker</span>
      <div class="title-row"><span class="title">Live board</span><span class="chain-chip" id="chainChip">&mdash;</span></div>
      <div class="conn-note"><span class="dot" id="connDot"></span> <span id="connNote">connecting&hellip;</span></div>
    </div>
    <div class="thresholds" id="thresholdSummary"></div>
  </div>

  <section>
    <h2><span>Tracked pairs</span><span id="pairCount" style="font-weight:400;color:var(--ink-faint);font-size:11.5px;"></span></h2>
    <div class="legend">
      <span><i class="swatch" style="background:transparent;box-shadow: inset 3px 0 0 var(--good-line);"></i>Currently qualifies as entry</span>
      <span><i class="swatch" style="background:transparent;box-shadow: inset 3px 0 0 var(--warn-line);"></i>Pulled back from peak -- exit zone</span>
    </div>
    <table>
      <thead>
        <tr>
          <th>Token</th><th>Price</th><th>Gain</th><th>Vol&times;</th><th>From peak</th><th>Liquidity</th><th>Tracked</th><th></th>
        </tr>
      </thead>
      <tbody id="pairRows"></tbody>
    </table>
    <p class="empty" id="emptyPairs" style="display:none;">No pairs currently meet the liquidity/volume/age floor -- widen your thresholds or wait for the next discovery cycle.</p>
  </section>

  <section>
    <h2>Live alert feed <span id="alertCount" style="font-weight:400;color:var(--ink-faint);font-size:11.5px;"></span></h2>
    <div id="alertFeed"><p class="empty">No entry or exit signals yet this session.</p></div>
  </section>

  <footer>Served locally by <span class="mono">memecoin-agent app</span> &middot; monitoring only, no wallet keys, no trades placed &middot; not financial advice</footer>
</div>
<div class="toast" id="toast"></div>

<script>
let DATA = { chain_id: '', thresholds: {}, pairs: [], alerts: [] };

const fmtPrice = (n) => {
  n = n ?? 0;
  if (n === 0) return '0';
  const decimals = n < 0.01 ? 8 : (n < 1 ? 6 : 2);
  return '$' + n.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
};
const fmtUsd = (n) => '$' + (n ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 });
const fmtAge = (ms) => {
  const mins = Math.max(0, Math.round((Date.now() - ms) / 60000));
  if (mins < 60) return mins + 'm';
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + 'h' + (mins % 60) + 'm';
  return Math.floor(hrs / 24) + 'd' + (hrs % 24) + 'h';
};
const fmtTimeShort = (ms) => new Date(ms).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
const esc = (s) => (s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function setConn(live) {
  document.getElementById('connDot').classList.toggle('live', live);
  document.getElementById('connNote').textContent = live ? 'live' : 'connecting…';
}

function renderMasthead() {
  document.getElementById('chainChip').textContent = DATA.chain_id || '—';
  const t = DATA.thresholds || {};
  document.getElementById('thresholdSummary').innerHTML =
    `Entry: gain &ge; <b>${t.gain_min_pct ?? '—'}%</b>, volume &ge; <b>${t.volume_multiplier ?? '—'}&times;</b><br>` +
    `Exit: pullback &ge; <b>${t.exit_drawdown_pct ?? '—'}%</b> from peak`;
}

function rowClass(p, t) {
  const entryQualifies = p.gain_pct >= (t.gain_min_pct ?? Infinity) && p.volume_multiplier >= (t.volume_multiplier ?? Infinity);
  const exitQualifies = p.drawdown_from_peak_pct >= (t.exit_drawdown_pct ?? Infinity);
  if (exitQualifies) return 'exit-zone';
  if (entryQualifies) return 'entry-zone';
  return '';
}

function renderPairs() {
  const t = DATA.thresholds || {};
  const pairs = [...DATA.pairs].sort((a, b) => b.gain_pct - a.gain_pct);
  document.getElementById('pairCount').textContent = pairs.length ? `${pairs.length} tracked` : '';
  document.getElementById('emptyPairs').style.display = pairs.length ? 'none' : 'block';

  document.getElementById('pairRows').innerHTML = pairs.map((p) => {
    const inZone = p.gain_pct >= (t.gain_min_pct ?? Infinity) && p.gain_pct <= (t.gain_target_max_pct ?? -Infinity);
    const gainClass = p.gain_pct >= (t.gain_min_pct ?? Infinity) ? 'hot' : 'cold';
    const volHot = p.volume_multiplier >= (t.volume_multiplier ?? Infinity);
    const exitHot = p.drawdown_from_peak_pct >= (t.exit_drawdown_pct ?? Infinity);
    return `<tr class="${rowClass(p, t)}">
      <td><div class="sym"><span class="name">${esc(p.symbol)}${inZone ? '<span class="badge zone">TARGET</span>' : ''}${exitHot ? '<span class="badge exit">EXIT</span>' : ''}</span><span class="dex">${esc(p.dex_id)} &middot; ${esc(p.chain_id)}</span></div></td>
      <td class="mono">${fmtPrice(p.price_usd)}</td>
      <td class="mono pct ${gainClass}">${p.gain_pct >= 0 ? '+' : ''}${p.gain_pct.toFixed(1)}%</td>
      <td class="mono vmul ${volHot ? 'hot' : ''}">${p.volume_multiplier.toFixed(1)}&times;</td>
      <td class="mono drawdown ${exitHot ? 'hot' : ''}">${p.drawdown_from_peak_pct > 0 ? '-' + p.drawdown_from_peak_pct.toFixed(1) + '%' : '—'}</td>
      <td class="mono">${fmtUsd(p.liquidity_usd)}</td>
      <td class="mono">${fmtAge(p.first_seen_ms)}</td>
      <td><a class="chart-link" href="${esc(p.url)}" target="_blank" rel="noopener">Chart&nbsp;&rarr;</a></td>
    </tr>`;
  }).join('');
}

function renderAlerts() {
  const alerts = DATA.alerts || [];
  document.getElementById('alertCount').textContent = alerts.length ? `${alerts.length} this session` : '';
  if (!alerts.length) {
    document.getElementById('alertFeed').innerHTML = '<p class="empty">No entry or exit signals yet this session.</p>';
    return;
  }
  document.getElementById('alertFeed').innerHTML = alerts.map((a) => {
    const label = a.event === 'pullback' ? 'EXIT' : 'ENTRY';
    return `<div class="alert-card">
      <span class="ev ${esc(a.event)}">${label}</span>
      <span class="msg">${esc(a.message.replace(a.url, '').trim())}</span>
      <span class="t">${fmtTimeShort(a.timestamp_ms)}</span>
      <a class="chart-link" href="${esc(a.url)}" target="_blank" rel="noopener">Open&nbsp;&rarr;</a>
    </div>`;
  }).join('');
}

function renderAll() {
  renderMasthead();
  renderPairs();
  renderAlerts();
}

function showToast(a) {
  const t = document.getElementById('toast');
  t.textContent = a.message;
  t.className = 'toast show ' + (a.event === 'pullback' ? 'exit' : 'entry');
  clearTimeout(showToast._h);
  showToast._h = setTimeout(() => { t.classList.remove('show'); }, 5000);
}

async function boot() {
  const res = await fetch('/state');
  DATA = await res.json();
  renderAll();

  const es = new EventSource('/events');
  es.onopen = () => setConn(true);
  es.onerror = () => setConn(false);
  es.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === 'pairs') {
      DATA.chain_id = msg.chain_id;
      DATA.thresholds = msg.thresholds;
      DATA.pairs = msg.pairs;
      DATA.alerts = msg.alerts;
      renderAll();
    } else if (msg.type === 'alert') {
      DATA.alerts.unshift(msg);
      DATA.alerts = DATA.alerts.slice(0, 50);
      renderAlerts();
      showToast(msg);
    }
  };
}
boot();
</script>
</body>
</html>
"""
