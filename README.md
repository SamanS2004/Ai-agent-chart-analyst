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

Common flags (available on all subcommands): `--symbol`, `--interval`,
`--category` (Bybit product type, default `linear`), `--journal-dir`.

## Journal output

Each cycle appends a JSON line to `data/journal/<YYYY-MM-DD>.jsonl` with
the unmitigated FVGs/order blocks seen, the confluence-zone count, and the
trade idea (if any) — entry, stop, targets, confidence, and rationale.

## Project layout

```
trading_agent/
  models.py       dataclasses: Candle, FairValueGap, OrderBlock, TradeIdea, ...
  bybit_client.py public Bybit v5 kline fetcher
  smc.py          FVG / order block / confluence-zone detection
  strategy.py     turns detected zones into a single trade idea
  session.py      6-9am Pacific window logic (DST-aware)
  journal.py      JSONL trade journal
  agent.py        orchestrates one cycle, or a full session loop
  tv_webhook.py   optional TradingView alert webhook receiver
  cli.py          `once` / `loop` / `webhook` commands
tests/            unit tests (FVG/OB detection, strategy, session, Bybit client parsing)
```

## Tests

```bash
python -m pytest
```

All detection/strategy/session/client-parsing logic is covered with
synthetic data, so the suite runs with no network access.
