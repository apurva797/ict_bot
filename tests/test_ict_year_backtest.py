import pandas as pd
import pytest

from ui.research.year_backtest_screen import validate_historical_coverage


def full_frame(start="2025-01-01T00:00:00Z", count=4, timeframe="1h"):
    freq = {"5m": "5min", "15m": "15min", "1h": "1h"}[timeframe]
    index = pd.date_range(start, periods=count, freq=freq, tz="UTC")
    return pd.DataFrame({"open": 1.0, "high": 1.1, "low": 0.9,
                         "close": 1.0, "volume": 10.0}, index=index)


def test_coverage_accepts_exact_full_interval():
    frame = full_frame(count=4)
    start = frame.index[0]
    end = frame.index[-1] + pd.Timedelta(hours=1)
    result = validate_historical_coverage(frame, start, end, "1h")
    assert result["actual_candles"] == 4
    assert result["coverage_pct"] == 100.0


def test_coverage_rejects_missing_candles():
    frame = full_frame(count=4).drop(pd.Timestamp("2025-01-01T02:00:00Z"))
    start = pd.Timestamp("2025-01-01T00:00:00Z")
    end = pd.Timestamp("2025-01-01T04:00:00Z")
    with pytest.raises(ValueError, match="Incomplete historical coverage"):
        validate_historical_coverage(frame, start, end, "1h")


def test_coverage_rejects_duplicate_timestamps():
    frame = full_frame(count=4)
    frame = pd.concat([frame, frame.iloc[[1]]]).sort_index()
    start = pd.Timestamp("2025-01-01T00:00:00Z")
    end = pd.Timestamp("2025-01-01T04:00:00Z")
    with pytest.raises(ValueError, match="duplicate"):
        validate_historical_coverage(frame, start, end, "1h")


def test_five_minute_full_year_exceeds_configured_history_limit():
    candles = int(pd.Timedelta(days=365) / pd.Timedelta(minutes=5))
    from demo_data import MAX_HISTORICAL_CANDLES
    assert candles > MAX_HISTORICAL_CANDLES


def test_ict_backtest_routes_existing_ict_signal_and_execution_costs(monkeypatch):
    import demo_backtest
    import strategies.ict

    frame = full_frame(count=110)
    calls = {}

    monkeypatch.setattr(strategies.ict, "ict_signal", lambda candles: {"side": "LONG"})

    def fake_execution(frame_arg, strategy, starting_capital, fee_rate, slippage, signal_sides):
        calls["strategy"] = strategy
        calls["fee_rate"] = fee_rate
        calls["slippage"] = slippage
        calls["signals"] = signal_sides
        return {"test": True}, pd.DataFrame(), pd.DataFrame()

    monkeypatch.setattr(demo_backtest, "run_backtest", fake_execution)
    metrics, equity, trades = demo_backtest.run_ict_backtest(
        frame, starting_capital=25000, fee_rate=0.0006, slippage=0.0005
    )
    assert metrics == {"test": True}
    assert calls["fee_rate"] == 0.0006
    assert calls["slippage"] == 0.0005
    assert calls["signals"].iloc[98] is None or pd.isna(calls["signals"].iloc[98])
    assert calls["signals"].iloc[99] == "BUY"
