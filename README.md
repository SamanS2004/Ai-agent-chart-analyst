# AI Chart Analyst

Two independent agents live in this repo:

- **FVG / Order Block agent for BTC** (below) — watches the BTC 15-minute
  chart during the 6-9am Pacific session for fair value gaps and order
  blocks.
- **[Memecoin Volume/Gain Tracker](#memecoin-volumegain-tracker-solana--robinhood-chain)**
  — watches well-established memecoins on Solana or Robinhood Chain in real
  time and alerts when volume picks up alongside a 10-15%+ price gain. Jump
  to its section below, or run it directly with
  `python run_solana_agent.py watch` (add `--chain-id robinhood` for
  Robinhood Chain).

## BTC FVG / Order Block agent

An agent that watches the BTC 15-minute chart during the 6:00-9:00am
Pacific session, looks for **fair value gaps (FVGs)** and **order blocks
(OBs)**, and journals a trade idea (entry / stop / targets / rationale)
whenever price sets up at an unmitigated zone.

## Data source: why Bybit instead of TradingView (and it doesn't have to be Bybit)

TradingView does not offer a public, headless API for pulling historical
OHLC candles into third-party code. Its "Advanced Charts" library is an
embeddable UI widget (for displaying charts inside your own app), and real
programmatic market-data access requires becoming an approved
broker/exchange integration — there's no free REST endpoint a script can
just call. So the default here is **Bybit's public v5 market-data API**,
which is free and requires no account or API key:

```
GET https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=15
```

If you *do* have TradingView Pro (or higher) and want TradingView in the
loop too, you can attach a webhook to a Pine Script alert and point it at
this agent's built-in webhook receiver (`trading-agent webhook`) — see
below. That gets you TradingView's alert engine feeding the same journal,
without needing a data API that doesn't exist for regular accounts.

**Bybit isn't required, though.** Every command takes `--data-source`, and
the client TradingAgent talks to is a small interface
(`get_klines(limit)` / `get_ticker_price()`) — swap the source without
touching the detection logic at all:

```bash
python run_agent.py --data-source bybit once        # default, no API key
python run_agent.py --data-source twelvedata once    # needs an API key, see below
```

**Twelve Data** (`trading_agent/twelvedata_client.py`) is the built-in
alternative — a plain REST client, independent of any exchange, that works
anywhere with normal internet access (unlike this repo's own sandboxed dev
session, which blocks outbound requests to *every* external host, Bybit
included — that's what prompted adding a second source). Get a free key at
[twelvedata.com](https://twelvedata.com) and either pass
`--twelvedata-api-key` or set `TWELVEDATA_API_KEY`. Its default symbol is
`BTC/USD` (vs. Bybit's `BTCUSDT`) — override with `--symbol` if needed.
Live `alerts` monitoring falls back to REST ticker polling automatically
for non-Bybit sources, since Twelve Data's free tier has no public
WebSocket here; pass `--price-source rest` explicitly if you want to be
sure.

Adding another source (Coinbase, Kraken, whatever you can reach) means
writing one small class with those same two methods — see
`bybit_client.py` or `twelvedata_client.py` for the shape.

## What it actually does (and doesn't do)

- Fetches recent 15m BTC candles from Bybit (default) or another configured source.
- Detects fair value gaps (3-candle imbalances) and order blocks (last
  opposite-colour candle before a break of market structure that also
  leaves a fair value gap immediately behind it — a same-direction swing
  break with no gap is not treated as a valid order block), tracking
  whether each has since been mitigated (price traded back through it) and,
  if so, whether that touch was a retest or a disrespect — see "Retests vs.
  disrespects" below for what that changes.
- When price is approaching a tradeable zone, it computes a trade idea:
  entry at the zone edge, stop beyond the zone, targets at 2R/3R, plus a
  plain-English rationale.
- FVGs that formed back to back during one strong push are treated as one
  continuous imbalance, not separate opportunities — see "Stacked FVGs"
  below for how the single priority zone is chosen.
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

## Stacked FVGs (`stacking.py`)

When several FVGs form back to back during one strong push, they're grouped
into one stack and only a single zone from it is ever offered as a trade
idea — never every gap in the stack independently. Priority within a stack:

1. **Discount/premium (hard filter).** A bullish FVG only counts if its
   midpoint sits in the lower half of the recent candle range (discount); a
   bearish one only in the upper half (premium). Fails this → dropped
   entirely, even if otherwise valid.
2. **Structurally nearest gap first.** The last gap formed during the push
   (highest top for a bullish stack, lowest bottom for a bearish one) is
   the first one price reaches on a retracement — that's the one to react
   at. If price has already traded through it, **the whole stack is
   disqualified that cycle**, not just that one gap — since each analysis
   cycle recomputes from scratch with no memory of "already tried and
   failed," this is what actually stops the tool from chasing a "better"
   fill deeper in the stack once the front of it has already failed.
3. **Order-block confluence beats plain imbalance**, but only among gaps
   still live per (2): if one of them overlaps a valid order block, that
   nearest *confluence* gap is nominated instead of the plain nearest one.

A gap's width relative to others in the same stack is reported (used in
the rationale, e.g. "the widest one in it") rather than used to override
the nearest-price pick — once discount/premium and confluence have already
picked a winner, folding in a third, differently-scaled criterion (price
distance vs. gap width) would make the result depend on arbitrary
weighting between them.

## Retests vs. disrespects (`smc.fvg_is_tradeable` / `smc.order_block_is_tradeable`)

The first candle that trades back into a zone is classified by how it left:

- **Retest** — it wicked into the zone and closed back out, respecting the
  level.
- **Disrespect** — it closed all the way through to the far side, breaking
  the level.

The two zone types treat that differently:

- **Fair value gaps are tradeable on either one.** The thesis for an FVG
  trade is that the inefficiency gets filled and reacted to — a clean
  retest and a disrespect-then-reversal both satisfy that, so a touched FVG
  stays a candidate, not just an untouched one.
- **Order blocks are only tradeable on a retest.** A disrespected order
  block — price closing straight through it — means that level failed as
  real structure and it's excluded for good; an untouched or once-retested
  block stays live.

This is a strictly geometric, close-price check (see `_classify_touch` in
`smc.py`), computed the moment a zone is first touched — it doesn't track
how many times a level has been retested since, just what the first touch
did.

## Setup

```bash
pip install -r requirements.txt
```

Requires network access to whichever data source you use (`api.bybit.com`
by default, `api.twelvedata.com` for `--data-source twelvedata`). Some
sandboxed/CI environments block outbound requests to *any* external host
by policy — if a command can't reach its data source, run it somewhere
with normal outbound HTTPS access, e.g. your own machine.

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

Watch it happen visually — a live-updating candlestick chart in your browser:

```bash
python run_agent.py app
```

Common flags (available on all subcommands): `--symbol`, `--interval`,
`--category` (Bybit product type, default `linear`), `--journal-dir`.

## Live chart app (`app` command)

Opens a local web page with a real-time candlestick chart: FVG and
order-block zones drawn to scale, the current trade plan overlaid (entry,
stop, targets, with the reward-to-risk ratio called out), and a live alert
feed — all updating as price moves, no manual refresh.

```bash
python run_agent.py app                     # http://127.0.0.1:8080
python run_agent.py app --port 9000
python run_agent.py --data-source twelvedata --twelvedata-api-key <key> app
```

This runs entirely on your own machine — there's no cloud dashboard or
third-party page involved, so it needs real network access to your chosen
data source just like `once`/`loop`/`alerts` do. Mechanically: a background
thread keeps the price/zones updated (same `fetch_and_detect` pipeline as
every other command, plus a `ZoneAlertEngine` for the live alert feed) and
pushes each change to your browser over Server-Sent Events; the page itself
is a small static HTML/JS file served from `trading_agent/live_app.py`, no
external JS framework. `--price-source`, `--recompute-seconds`, and
`--proximity-pct` work the same as on `alerts`.

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
  bybit_client.py      public Bybit v5 kline/ticker fetcher (default data source)
  twelvedata_client.py Twelve Data REST kline/ticker fetcher (alternative data source)
  smc.py          FVG / order block / confluence-zone detection
  stacking.py     groups back-to-back same-direction FVGs, picks the one priority zone
  strategy.py     turns detected/stacked zones into a single trade idea
  alerts.py       touch/retest/disrespect state machine + proximity filtering
  live_stream.py  Bybit public WebSocket ticker feed
  alert_sinks.py  console/journal/desktop-notification/webhook delivery
  session.py      6-9am Pacific window logic (DST-aware)
  journal.py      JSONL trade journal
  report.py       renders the journal into a self-contained HTML dashboard
  live_app.py     local live web app (real-time chart, SSE, stdlib HTTP server)
  agent.py        orchestrates one cycle, a session loop, live alerting, or the live app
  tv_webhook.py   optional TradingView alert webhook receiver
  cli.py          `once` / `loop` / `alerts` / `app` / `report` / `webhook` commands
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

---

# Memecoin Volume/Gain Tracker (Solana & Robinhood Chain)

A separate agent (`solana_agent/`) that watches **well-established**
memecoins on **Solana** or **Robinhood Chain** in real time and alerts the
moment a token's volume picks up **and** its price has run up roughly
10-15% since the agent started watching it -- the classic early-pump
shape, but only on coins that already have real liquidity and trading
history. Freshly launched tokens are excluded by default (see "Staying
away from new pairs" below) -- this agent is built for trading coins that
are already established, not sniping brand-new listings.

**This is a monitoring tool, not a trading bot.** There are no wallet keys,
no swap/transaction code, and no auto-buy path anywhere in this package --
it only watches public market data and tells you about it. Memecoins are
extremely high risk on either chain: most are unaudited, thinly traded,
and a meaningful share are outright rug pulls or wash-traded to fake
volume. A volume+price alert here is a "go look at this," not a signal to
buy, and nothing in this repo should be treated as financial advice.

## Chains supported (`--chain-id`)

| `--chain-id` | Chain | Where tokens trade |
| --- | --- | --- |
| `solana` (default) | Solana | Raydium, Orca, Meteora, pump.fun bonding curves (once graduated) |
| `robinhood` | [Robinhood Chain](https://blog.arbitrum.io/robinhood-chain-mainnet/) (an Arbitrum Orbit L2, on-chain id 4663) | Uniswap-family pools -- Robinhood's own tokenized-stock tokens (e.g. stock/ETF tokens) as well as independently launched community/meme tokens |

Everything else (discovery, liquidity/volume/age filtering, gain/volume
tracking, signals, alerts) works identically on either chain -- it's all
just DexScreener pair data keyed by `chain_id`, with no chain-specific
logic anywhere in the pipeline. `--watch-addresses` takes whichever address
format the selected chain uses (a base58 mint address on Solana, a `0x...`
contract address on Robinhood Chain).

Robinhood Chain launched its mainnet in mid-2026 and is much newer/smaller
than Solana's memecoin scene -- if `watch`/`once` isn't finding anything
there, try lowering `--min-liquidity-usd`, `--min-volume-h24-usd`, and/or
`--min-pair-age-days` from their defaults (tuned against Solana's deeper
market) to match what's actually available on-chain right now.

## Data source: DexScreener (free, no API key)

Unlike the BTC agent, there's no single "the" price feed for a memecoin --
each one trades on whatever DEX pool(s) it's listed on. [DexScreener](https://docs.dexscreener.com/api/reference)
indexes pools across dozens of chains, Solana and Robinhood Chain included,
and exposes it over a free, keyless REST API, which is what this agent
uses for everything: discovering trending tokens and pulling each pair's
live price/volume.

```
GET https://api.dexscreener.com/latest/dex/tokens/{addresses}   # price/volume for known tokens
GET https://api.dexscreener.com/token-boosts/latest/v1          # trending/boosted tokens (discovery)
GET https://api.dexscreener.com/token-profiles/latest/v1        # newest submitted token profiles (discovery, opt-in)
```

No wallet, no RPC node, and no paid data provider needed for either chain.
Like the Bybit-based BTC agent, this needs real outbound network access to
`api.dexscreener.com`, which some sandboxed/CI environments block by
policy -- run it somewhere with normal internet access if a request fails.

## How it decides what to watch

Every poll cycle, the agent's candidate list is:

1. Any addresses you pass with `--watch-addresses` (always tracked).
2. DexScreener's "boosted tokens" feed (latest + top), refreshed every
   `--discover-seconds` (default 300s) since discovery feeds have a lower
   rate limit than the price/volume endpoint.

The "newest submitted token profiles" feed is *not* used by default -- by
definition it surfaces brand-new listings, which this agent avoids (pass
`--include-new-listings` to opt in).

Candidates are resolved to trading pairs, deduplicated to the
highest-liquidity pool per token (a coin can list on several DEXes at
once), and filtered down to well-established coins: `--min-liquidity-usd`
(default $25,000) and `--min-volume-h24-usd` (default $20,000) screen out
pairs too thin for a "gain" to mean anything, on top of the pair-age filter
below.

## Staying away from new pairs

This agent is meant for coins that already have an established market, not
freshly launched tokens -- `--min-pair-age-days` (default **30**) drops any
pair younger than that. A pair with no creation timestamp at all is
dropped too rather than assumed established, since DexScreener not knowing
a pool's age is itself a sign it's too new or too thin to trust. Set
`--min-pair-age-days 0` to disable this filter if you do want to see new
listings.

## How the gain/volume signal works (`tracker.py` / `signals.py`)

- **Gain** is measured from the lowest price seen *since this agent started
  watching that pair*, not a fixed calendar window -- once at least 10
  minutes of local history exists, `(current - recent_low) / recent_low`
  replaces DexScreener's own `priceChange.h1` figure, which is used as a
  same-cycle estimate before that.
- **Volume multiplier** compares the last 5 minutes of volume against that
  pair's own recent baseline rate (again falling back to
  `volume.h1 / 12` as an hourly average until enough local samples exist).
- An alert fires once gain crosses `--gain-min-pct` (default 10%) *and*
  the volume multiplier crosses `--volume-multiplier` (default 2.0x) --
  the message also says whether it's within the 10-15% target zone or has
  run further. Each pair then latches so it doesn't re-alert every poll
  while it stays elevated; it re-arms only once its gain cools back down
  by `--reset-buffer-pct` (default 5 points) below the threshold, so a
  separate later pump still gets its own alert.

## Usage

```bash
pip install -r requirements.txt
```

Run one discover+poll cycle right now and print what's being tracked:

```bash
python run_solana_agent.py once
```

Watch continuously and alert in real time (Ctrl+C to stop):

```bash
python run_solana_agent.py watch                       # Solana (default)
python run_solana_agent.py --chain-id robinhood watch   # Robinhood Chain
```

Track specific tokens in addition to auto-discovered trending ones (comma
separated addresses, format matches the selected chain):

```bash
python run_solana_agent.py --watch-addresses <mint1>,<mint2> watch
python run_solana_agent.py --chain-id robinhood --watch-addresses 0xabc...,0xdef... watch
```

Tune the thresholds:

```bash
python run_solana_agent.py --gain-min-pct 10 --gain-target-max-pct 15 --volume-multiplier 2.5 watch
```

**Where alerts go:** always printed to the terminal (with a bell) and
logged to `data/memecoin_journal/<YYYY-MM-DD>.jsonl`. Add
`--desktop-notify` for a best-effort native OS notification, or
`--alert-webhook-url <url>` to POST each alert as JSON anywhere -- the
same ntfy.sh trick from the BTC agent works here too:
`--alert-webhook-url https://ntfy.sh/<your-topic>` (pipe through a small
relay if you need it reshaped into ntfy's plain-text body).

Common flags: `--chain-id`, `--min-liquidity-usd`, `--min-volume-h24-usd`,
`--min-pair-age-days`, `--no-boosted` / `--include-new-listings` (toggle
either discovery feed), `--journal-dir`, `--lookback-seconds` (how much
local history to keep per pair).

## Project layout

```
solana_agent/
  models.py             TokenPair, TrackedPair, Alert dataclasses
  dexscreener_client.py public DexScreener REST client (search/tokens/boosts/profiles)
  discovery.py          candidate-token discovery, dedup-to-highest-liquidity, dust filtering
  tracker.py            per-pair rolling history -> local gain %% / volume multiplier
  signals.py            threshold + latch/reset state machine -> Alert
  journal.py            JSONL alert/event journal
  alert_sinks.py         console/journal/desktop-notification/webhook delivery
  agent.py              orchestrates discovery + polling + tracking + signaling
  cli.py                `once` / `watch` commands
```

## Tests

Same command as the BTC agent (`python -m pytest` runs both suites).
`tests/test_solana_*.py` covers DexScreener response parsing, discovery
dedup/filtering, local gain and volume-multiplier math (including the
API-window fallback before enough local history exists), the alert
latch/reset state machine, the journal, and the polling/pruning wiring in
`agent.py` -- all against synthetic fixtures/fakes, so it runs with no
network access.
