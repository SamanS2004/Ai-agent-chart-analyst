# AI Chart Analyst — FVG / Order Block agent for BTC

An agent that watches the BTC 15-minute chart during the 6:00-9:00am
Pacific session, looks for **fair value gaps (FVGs)** and **order blocks
(OBs)**, and journals a trade idea (entry / stop / targets / rationale)
whenever price sets up at an unmitigated zone.

## Data source: why Bybit instead of TradingView

TradingView does not offer a public, headless API for pulling historical
OHLC candles into third-party code. Its "Advanced Charts" library is an
embeddable UI widget (for displaying charts inside your own app), and real
programmatic market-data access requires becoming an approved
broker/exchange integration — there's no free REST endpoint a script can
just call. So, per the fallback you described, this agent pulls candles
directly from **Bybit's public v5 market-data API**, which is free and
requires no account or API key for market data:

```
GET https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=15
```

If you *do* have TradingView Pro (or higher) and want TradingView in the
loop too, you can attach a webhook to a Pine Script alert and point it at
this agent's built-in webhook receiver (`trading-agent webhook`) — see
below. That gets you TradingView's alert engine feeding the same journal,
without needing a data API that doesn't exist for regular accounts.

## What it actually does (and doesn't do)

- Fetches recent 15m BTCUSDT candles from Bybit.
- Detects fair value gaps (3-candle imbalances) and order blocks (last
  opposite-colour candle before a break of market structure), tracking
  whether each has since been mitigated (price traded back through it).
- When price is approaching an unmitigated zone, it computes a trade idea:
  entry at the zone edge, stop beyond the zone, targets at 2R/3R, plus a
  plain-English rationale.
- Writes everything to a daily JSONL journal under `data/journal/`.
- **It does not place real orders.** There are no exchange API keys, no
  order-execution code, and no live-trading path in this repo. It's an
  analysis/paper-trading assistant — "become a trader" here means it
  reasons and journals like one, not that it risks real money. Wiring it
  up to place live orders would be a separate, explicit step with its own
  risk controls, and isn't something to do without deliberately deciding
  to.
- This is not financial advice; FVG/order-block heuristics are a
  simplified, from-scratch interpretation of common ICT-style concepts,
  not a guaranteed-profitable strategy.

## Setup

```bash
pip install -r requirements.txt
```

Requires network access to `api.bybit.com`. (Note: some sandboxed/CI
environments block outbound requests to arbitrary hosts by policy — if
`trading-agent once` can't reach Bybit, run it somewhere with normal
outbound HTTPS access, e.g. your own machine.)

## Usage

Run one analysis cycle right now (useful for testing, any time of day):

```bash
python run_agent.py once
```

Run continuously, only analyzing every 15 minutes while it's 6-9am
Pacific (sleeps/idles outside that window, correctly handling
PST/PDT since it uses IANA tz data):

```bash
python run_agent.py loop
```

Optional: receive TradingView alert webhooks into the same journal:

```bash
python run_agent.py webhook --port 8765 --secret <your-shared-secret>
```

Then in TradingView, create an alert with webhook URL
`http://<host>:8765` and, if you set `--secret`, add header
`X-Webhook-Secret: <your-shared-secret>`.

Get live alerts the moment price touches, retests, or disrespects a zone:

```bash
python run_agent.py alerts
```

Common flags (available on all subcommands): `--symbol`, `--interval`,
`--category` (Bybit product type, default `linear`), `--journal-dir`.

## Live alerts (`alerts` command)

Watches price in real time against every FVG/order-block zone and fires an
alert the instant one of these happens:

- **touch** — price first trades into an unmitigated zone.
- **retest** — price touches the same zone again after having left it (the
  zone held in between).
- **disrespect** — price breaks through the far edge of the zone (not just
  a wick — beyond a small buffer), meaning it failed as support/resistance.
  Once a zone is disrespected it's retired and won't alert again.

```bash
python run_agent.py alerts                          # WebSocket price feed (default, lowest latency)
python run_agent.py alerts --price-source rest       # REST ticker polling instead (every 5s by default)
python run_agent.py alerts --always-on               # monitor around the clock, not just 6-9am Pacific
python run_agent.py alerts --proximity-pct 0.02       # only track zones within 2% of price (default 5%)
```

By default zones are recomputed from fresh candles every 60s
(`--recompute-seconds`), and only zones within 5% of the current price are
tracked (`--proximity-pct`) — with a multi-day candle window there can be
dozens of old, far-away zones, and without this filter they'd flood you
with irrelevant alerts.

**Where the alert goes:**
- Always: printed to the terminal (with a bell) and logged to the journal.
- `--desktop-notify`: best-effort native OS notification (macOS/Linux/Windows).
- `--alert-webhook-url <url>`: POSTs the alert as JSON to any URL you give
  it. The easiest way to get it on your phone with zero setup is
  [ntfy.sh](https://ntfy.sh) — pick a topic name and pass
  `--alert-webhook-url https://ntfy.sh/<your-topic>` (note ntfy expects
  plain text, not JSON, as its body, so for ntfy specifically pipe alerts
  through a small relay, or just watch the terminal/desktop notification).
  For Discord/Slack, point it at an incoming-webhook relay that reshapes
  the JSON, or write your own tiny receiver — the payload is the `Alert`
  dataclass as JSON (`event`, `kind`, `label`, `top`, `bottom`, `price`,
  `timestamp_ms`, `message`).

## Dashboard (`report` command)

Renders the local journal into a single self-contained HTML file — price
chart, trade idea ledger, and the alert tape — no network or server needed,
just open it in a browser:

```bash
python run_agent.py report                              # writes data/dashboard.html
python run_agent.py report --output today.html --start 2026-09-15 --end 2026-09-15
```

## Running it every day (your own machine)

This has to run somewhere with real outbound access to `api.bybit.com` —
sandboxed dev/CI environments often block that by policy. Run it on your
own machine, a VPS, or any host with normal internet access.

`trading-agent loop` and `trading-agent alerts` already gate themselves to
the 6-9am Pacific window internally (DST-aware), so the simplest setup is
just: keep the process running continuously, and it idles itself outside
the session. Pick whichever fits your OS:

**macOS (launchd)** — save as `~/Library/LaunchAgents/com.you.trading-agent.plist`,
substituting your repo path, then `launchctl load` it:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.you.trading-agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/path/to/Ai-agent-chart-analyst/run_agent.py</string>
    <string>loop</string>
  </array>
  <key>WorkingDirectory</key><string>/path/to/Ai-agent-chart-analyst</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/trading-agent.log</string>
  <key>StandardErrorPath</key><string>/tmp/trading-agent-error.log</string>
</dict>
</plist>
```

**Linux (systemd --user)** — save as `~/.config/systemd/user/trading-agent.service`:

```ini
[Unit]
Description=BTC FVG/order-block trading agent

[Service]
WorkingDirectory=/path/to/Ai-agent-chart-analyst
ExecStart=/usr/bin/python3 run_agent.py loop
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
```

Then: `systemctl --user enable --now trading-agent.service`

**Either OS, simpler but less robust** — just run `trading-agent loop` (or
`alerts`) in a `tmux`/`screen` session and leave it attached.

**Daily dashboard** — generate a fresh `report` once the session closes.
Cron understands `CRON_TZ` (Linux; on macOS use `TZ=` and adjust for UTC
manually, since launchd's calendar trigger has no timezone support beyond
the system clock):

```cron
CRON_TZ=America/Los_Angeles
5 9 * * * cd /path/to/Ai-agent-chart-analyst && /usr/bin/python3 run_agent.py report --output "data/dashboard-$(date +\%F).html"
```

**Windows** — Task Scheduler, action `python.exe run_agent.py loop`,
trigger "At log on" with "Repeat task" disabled (`loop` runs forever on
its own).

## Journal output

Each cycle appends a JSON line to `data/journal/<YYYY-MM-DD>.jsonl` with
the unmitigated FVGs/order blocks seen, the confluence-zone count, and the
trade idea (if any) — entry, stop, targets, confidence, and rationale.

## Project layout

```
trading_agent/
  models.py       dataclasses: Candle, FairValueGap, OrderBlock, TradeIdea, ...
  bybit_client.py public Bybit v5 kline/ticker fetcher
  smc.py          FVG / order block / confluence-zone detection
  strategy.py     turns detected zones into a single trade idea
  alerts.py       touch/retest/disrespect state machine + proximity filtering
  live_stream.py  Bybit public WebSocket ticker feed
  alert_sinks.py  console/journal/desktop-notification/webhook delivery
  session.py      6-9am Pacific window logic (DST-aware)
  journal.py      JSONL trade journal
  report.py       renders the journal into a self-contained HTML dashboard
  agent.py        orchestrates one cycle, a session loop, or live alerting
  tv_webhook.py   optional TradingView alert webhook receiver
  cli.py          `once` / `loop` / `alerts` / `report` / `webhook` commands
tests/            unit tests (FVG/OB detection, strategy, alerts, session, Bybit client parsing)
```

## Tests

```bash
python -m pytest
```

All detection/strategy/session/client-parsing logic is covered with
synthetic data, so the suite runs with no network access. There's also a
regression fixture (`tests/fixtures/btc_15m_2026-09-13_to_15.csv`) captured
from a real BTC/USD 15m feed, validated against the detection pipeline and
spot-checked candle-by-candle against each zone's own definition -- this
catches edge cases (real precision, real volatility clustering) that
hand-built synthetic candles tend not to.
