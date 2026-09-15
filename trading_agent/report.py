"""Renders the JSONL trade journal into a single self-contained HTML dashboard.

No network calls, no external assets -- everything (styles, the price
sparkline) is inlined so the file opens straight from disk.
"""

from __future__ import annotations

import html
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

BULLISH_COLOR = "#1a7f4e"
BEARISH_COLOR = "#b3261e"
NEUTRAL_COLOR = "#5b6472"


def load_journal_records(
    directory: Path, start: date | None = None, end: date | None = None
) -> list[dict]:
    records: list[dict] = []
    for path in sorted(Path(directory).glob("*.jsonl")):
        try:
            file_date = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if start and file_date < start:
            continue
        if end and file_date > end:
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    records.sort(key=lambda r: r.get("logged_at", ""))
    return records


def _record_type(record: dict) -> str:
    return record.get("type") or ("analysis" if "last_price" in record else "event")


def summarize(records: list[dict]) -> dict:
    analyses = [r for r in records if _record_type(r) == "analysis"]
    alerts = [r for r in records if _record_type(r) == "alert"]
    ideas = [r["trade_idea"] for r in analyses if r.get("trade_idea")]

    return {
        "analysis_count": len(analyses),
        "alert_count": len(alerts),
        "alert_breakdown": dict(Counter(a["event"] for a in alerts)),
        "idea_count": len(ideas),
        "idea_breakdown": dict(Counter(i["kind"] for i in ideas)),
        "price_series": [
            (a["timestamp_ms"], a["last_price"]) for a in analyses if a.get("last_price")
        ],
        "analyses": analyses,
        "alerts": alerts,
        "ideas": ideas,
    }


def _sparkline_svg(price_series: list[tuple[int, float]], width: int = 640, height: int = 120) -> str:
    if len(price_series) < 2:
        return '<p class="empty">Not enough price history yet for a chart.</p>'

    prices = [p for _, p in price_series]
    lo, hi = min(prices), max(prices)
    span = (hi - lo) or 1.0
    pad = 8
    n = len(prices)

    def x_at(i: int) -> float:
        return pad + (width - 2 * pad) * i / (n - 1)

    def y_at(p: float) -> float:
        return height - pad - (height - 2 * pad) * (p - lo) / span

    points = " ".join(f"{x_at(i):.1f},{y_at(p):.1f}" for i, p in enumerate(prices))
    last_x, last_y = x_at(n - 1), y_at(prices[-1])
    color = BULLISH_COLOR if prices[-1] >= prices[0] else BEARISH_COLOR

    return f"""
<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img"
     aria-label="Last price over the selected period" preserveAspectRatio="none">
  <polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"
            stroke-linejoin="round" stroke-linecap="round" />
  <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3.5" fill="{color}" />
</svg>
<div class="spark-labels"><span>{lo:,.2f}</span><span>{hi:,.2f}</span></div>
""".strip()


def _fmt_ts(timestamp_ms: int | None) -> str:
    if not timestamp_ms:
        return "--"
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M")


def _idea_rows(ideas: list[dict]) -> str:
    if not ideas:
        return '<tr><td colspan="6" class="empty">No trade ideas logged yet.</td></tr>'
    rows = []
    for idea in reversed(ideas):
        color = BULLISH_COLOR if idea["kind"] == "long" else BEARISH_COLOR
        rows.append(
            f"<tr><td>{_fmt_ts(idea.get('timestamp_ms'))}</td>"
            f"<td><span class='pill' style='background:{color}'>{html.escape(idea['kind'].upper())}</span></td>"
            f"<td>{idea['confidence']}</td>"
            f"<td>{idea['entry']:,.2f}</td>"
            f"<td>{idea['stop']:,.2f}</td>"
            f"<td>{', '.join(f'{t:,.2f}' for t in idea['targets'])}</td></tr>"
        )
    return "\n".join(rows)


def _alert_rows(alerts: list[dict]) -> str:
    if not alerts:
        return '<tr><td colspan="4" class="empty">No live alerts logged yet.</td></tr>'
    rows = []
    for alert in reversed(alerts):
        badge_color = {
            "touch": NEUTRAL_COLOR,
            "retest": BULLISH_COLOR if alert["kind"] == "bullish" else BEARISH_COLOR,
            "disrespect": "#8a5a00",
        }.get(alert["event"], NEUTRAL_COLOR)
        rows.append(
            f"<tr><td>{_fmt_ts(alert.get('timestamp_ms'))}</td>"
            f"<td><span class='pill' style='background:{badge_color}'>{html.escape(alert['event'])}</span></td>"
            f"<td>{html.escape(alert['label'])} ({alert['kind']})</td>"
            f"<td>{html.escape(alert['message'])}</td></tr>"
        )
    return "\n".join(rows)


def render_dashboard_html(records: list[dict], symbol: str = "BTCUSDT") -> str:
    s = summarize(records)
    idea_breakdown = ", ".join(f"{k}: {v}" for k, v in s["idea_breakdown"].items()) or "none"
    alert_breakdown = ", ".join(f"{k}: {v}" for k, v in s["alert_breakdown"].items()) or "none"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<title>{html.escape(symbol)} trading journal</title>
<style>
  :root {{ color-scheme: light dark; padding-top: env(safe-area-inset-top, 0px); padding-bottom: env(safe-area-inset-bottom, 0px); }}
  body {{ margin: 0; padding: 24px 16px; font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #f7f7f8; color: #1b1d21; }}
  @media (prefers-color-scheme: dark) {{ body {{ background: #14161a; color: #e7e9ec; }} }}
  .wrap {{ max-width: 900px; margin: 0 auto; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .subtitle {{ color: #6b7280; margin: 0 0 24px; font-size: 13px; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 24px; }}
  .card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 14px; }}
  @media (prefers-color-scheme: dark) {{ .card {{ background: #1d2025; border-color: #2c3038; }} }}
  .card .label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: #6b7280; }}
  .card .value {{ font-size: 22px; font-weight: 600; margin-top: 4px; }}
  .card .detail {{ font-size: 12px; color: #6b7280; margin-top: 4px; }}
  section {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 16px; margin-bottom: 20px;
            overflow-x: auto; }}
  @media (prefers-color-scheme: dark) {{ section {{ background: #1d2025; border-color: #2c3038; }} }}
  section h2 {{ font-size: 14px; margin: 0 0 12px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid #eef0f2; white-space: nowrap; }}
  @media (prefers-color-scheme: dark) {{ th, td {{ border-color: #2c3038; }} }}
  th {{ color: #6b7280; font-weight: 500; font-size: 11px; text-transform: uppercase; }}
  td:last-child {{ white-space: normal; }}
  .pill {{ color: #fff; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }}
  .empty {{ color: #6b7280; font-style: italic; }}
  .spark-labels {{ display: flex; justify-content: space-between; font-size: 11px; color: #6b7280; margin-top: 4px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>{html.escape(symbol)} trading journal</h1>
  <p class="subtitle">FVG / order-block analysis &amp; live alerts &mdash; generated from the local journal</p>

  <div class="cards">
    <div class="card"><div class="label">Analysis cycles</div><div class="value">{s['analysis_count']}</div></div>
    <div class="card"><div class="label">Live alerts</div><div class="value">{s['alert_count']}</div>
      <div class="detail">{html.escape(alert_breakdown)}</div></div>
    <div class="card"><div class="label">Trade ideas</div><div class="value">{s['idea_count']}</div>
      <div class="detail">{html.escape(idea_breakdown)}</div></div>
  </div>

  <section>
    <h2>Price</h2>
    {_sparkline_svg(s['price_series'])}
  </section>

  <section>
    <h2>Trade ideas</h2>
    <table>
      <thead><tr><th>Time</th><th>Side</th><th>Confidence</th><th>Entry</th><th>Stop</th><th>Targets</th></tr></thead>
      <tbody>{_idea_rows(s['ideas'])}</tbody>
    </table>
  </section>

  <section>
    <h2>Live alerts</h2>
    <table>
      <thead><tr><th>Time</th><th>Event</th><th>Zone</th><th>Message</th></tr></thead>
      <tbody>{_alert_rows(s['alerts'])}</tbody>
    </table>
  </section>
</div>
</body>
</html>
"""
