"""Regression test against a real BTC/USD 15m snapshot (Twelve Data, 2026-09-13/15
UTC), not synthetic data. Synthetic unit tests exercise the definitions in
isolation; this exercises the full pipeline against real precision, real
volatility clustering, and real candle spacing -- the kind of input that
turns up edge cases hand-built fixtures don't."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from trading_agent.models import Candle
from trading_agent.smc import find_confluence_zones, find_fair_value_gaps, find_order_blocks
from trading_agent.strategy import generate_trade_idea

FIXTURE = Path(__file__).parent / "fixtures" / "btc_15m_2026-09-13_to_15.csv"


def _load_candles() -> list[Candle]:
    rows = []
    with FIXTURE.open() as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            dt = datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            rows.append(
                Candle(
                    timestamp_ms=int(dt.timestamp() * 1000),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=0.0,
                )
            )
    rows.reverse()  # file is newest-first, like Bybit's raw response
    return rows


def test_fixture_is_well_formed_real_data():
    candles = _load_candles()
    assert len(candles) == 200

    for i in range(1, len(candles)):
        assert candles[i].timestamp_ms - candles[i - 1].timestamp_ms == 15 * 60 * 1000

    for c in candles:
        assert c.low <= c.open <= c.high
        assert c.low <= c.close <= c.high


def test_detection_pipeline_runs_clean_on_real_data():
    candles = _load_candles()

    fvgs = find_fair_value_gaps(candles)
    order_blocks = find_order_blocks(candles)
    zones = find_confluence_zones(fvgs, order_blocks)
    idea = generate_trade_idea(candles, fvgs, order_blocks, zones)

    for f in fvgs:
        assert f.top >= f.bottom
        assert f.kind in ("bullish", "bearish")
    for o in order_blocks:
        assert o.top >= o.bottom
        assert o.kind in ("bullish", "bearish")
    for z in zones:
        assert z.top >= z.bottom
        assert z.kind == z.fvg.kind == z.order_block.kind

    if idea is not None:
        assert idea.kind in ("long", "short")
        assert idea.risk > 0
        if idea.kind == "long":
            assert idea.stop < idea.entry < min(idea.targets)
        else:
            assert idea.targets and max(idea.targets) < idea.entry < idea.stop


def test_detected_zone_matches_its_own_definition():
    # A bearish FVG at candles[i-2:i+1] means candles[i-2].low > candles[i].high --
    # spot-check one against the raw fixture data rather than trusting the
    # counts alone, since matching counts could still hide an off-by-one.
    candles = _load_candles()
    fvgs = find_fair_value_gaps(candles)

    bearish = next(f for f in fvgs if f.kind == "bearish")
    first, third = candles[bearish.index - 2], candles[bearish.index]
    assert first.low > third.high
    assert bearish.top == first.low
    assert bearish.bottom == third.high

    bullish = next(f for f in fvgs if f.kind == "bullish")
    first, third = candles[bullish.index - 2], candles[bullish.index]
    assert first.high < third.low
    assert bullish.top == third.low
    assert bullish.bottom == first.high


def test_counts_are_stable_snapshot():
    """Pins current behavior against this exact dataset. A change here means
    the detection logic changed -- update deliberately, don't just re-pin."""
    candles = _load_candles()
    fvgs = find_fair_value_gaps(candles)
    order_blocks = find_order_blocks(candles)
    zones = find_confluence_zones(fvgs, order_blocks)

    assert len(fvgs) == 52
    assert sum(not f.mitigated for f in fvgs) == 13
    assert len(order_blocks) == 13
    assert sum(not o.mitigated for o in order_blocks) == 0
    assert len(zones) == 0
