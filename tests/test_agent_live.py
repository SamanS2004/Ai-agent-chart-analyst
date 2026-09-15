from trading_agent.agent import TradingAgent
from trading_agent.journal import TradeJournal
from trading_agent.models import Candle


def candle(ts, o, h, l, cl, v=1.0):
    return Candle(timestamp_ms=ts, open=o, high=h, low=l, close=cl, volume=v)


class FakeClient:
    def __init__(self, candles, prices):
        self.candles = candles
        self.prices = list(prices)

    def get_klines(self, **kwargs):
        return self.candles

    def get_ticker_price(self, **kwargs):
        return self.prices.pop(0)


def _bullish_fvg_candles():
    # first.high(101) < third.low(108) -> bullish FVG zone [101, 108]
    return [
        candle(0, 100, 101, 99, 100.5),
        candle(1, 100.5, 110, 100, 109),
        candle(2, 109, 115, 108, 114),
    ]


def test_rest_mode_emits_touch_then_retest(tmp_path):
    prices = [110, 105, 112, 104]  # outside, touch, exit (held), retest
    client = FakeClient(_bullish_fvg_candles(), prices)
    journal = TradeJournal(tmp_path)
    agent = TradingAgent(client=client, journal=journal)

    collected = []
    agent.run_live_alerts(
        on_alert=collected.append,
        price_source="rest",
        recompute_seconds=60,
        price_poll_seconds=5,
        always_on=True,
        proximity_pct=0,  # this test is about touch/retest logic, not proximity filtering
        sleep_fn=lambda _seconds: None,
        max_price_ticks=4,
    )

    assert [a.event for a in collected] == ["touch", "retest"]


def test_rest_mode_skips_work_outside_session_window(tmp_path, monkeypatch):
    prices = [105, 105]
    client = FakeClient(_bullish_fvg_candles(), prices)
    journal = TradeJournal(tmp_path)
    agent = TradingAgent(client=client, journal=journal)

    monkeypatch.setattr("trading_agent.agent.is_session_active", lambda tz=None: False)

    collected = []
    agent.run_live_alerts(
        on_alert=collected.append,
        price_source="rest",
        always_on=False,
        sleep_fn=lambda _seconds: None,
        max_price_ticks=2,
    )

    assert collected == []
    assert client.prices == prices  # ticker was never even polled
