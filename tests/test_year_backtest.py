import numpy as np
import pandas as pd
import pytest

from engine.year_backtest import BacktestConfig, normalize_ohlcv, generate_signals, run_backtest


def sample_frame(n=1200):
    rng = np.random.default_rng(7)
    returns = rng.normal(0.00015, 0.004, n)
    close = 50000 * np.exp(np.cumsum(returns))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.maximum(close * 0.001, 1.0)
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    return pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC"),
        "open": open_, "high": high, "low": low, "close": close,
        "volume": rng.uniform(1, 100, n),
    })


def test_normalize_removes_duplicate_timestamps():
    frame = sample_frame(200)
    frame = pd.concat([frame, frame.iloc[[5]]], ignore_index=True)
    normalized = normalize_ohlcv(frame)
    assert len(normalized) == 200
    assert normalized.timestamp.is_monotonic_increasing


def test_generate_signals_has_only_valid_signal_values():
    config = BacktestConfig(warmup_bars=100)
    signals = generate_signals(sample_frame(), config)
    assert set(signals.signal.unique()).issubset({-1, 0, 1})


def test_backtest_returns_metrics_and_trade_schema():
    config = BacktestConfig(warmup_bars=100, starting_capital=10000, commission_rate=0.0001, slippage_rate=0.0001)
    result = run_backtest(sample_frame(), config, strategy="ema_trend")
    assert result.metrics["starting_capital"] == 10000
    assert "total_return_pct" in result.metrics
    assert "max_drawdown_pct" in result.metrics
    assert len(result.equity_curve) == 1200
    if result.trades:
        assert "net_pnl" in result.trades[0]
        assert "exit_reason" in result.trades[0]


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        BacktestConfig(days=500).validate()


def test_year_backtest_bybit_fallback_and_exact_coverage(tmp_path, monkeypatch):
    from engine import year_backtest as yb

    index = pd.date_range("2025-01-01T00:00:00Z", periods=24, freq="1h")
    frame = pd.DataFrame({
        "timestamp": index,
        "open": np.full(24, 100.0),
        "high": np.full(24, 102.0),
        "low": np.full(24, 99.0),
        "close": np.full(24, 101.0),
        "volume": np.full(24, 10.0),
    })
    calls = []

    def fake_exchange(exchange_id, symbol, timeframe, start_ms, end_ms, rate_limit=True):
        calls.append(exchange_id)
        if exchange_id == "binance":
            return frame.iloc[:-2].copy()
        if exchange_id == "bybit":
            return frame.copy()
        raise AssertionError(f"Unexpected provider: {exchange_id}")

    monkeypatch.setattr(yb, "_exchange_ohlcv", fake_exchange)
    config = yb.BacktestConfig(
        symbol="BTC/USDT", timeframe="1h", days=1,
        exchange_id="binance", data_source="auto",
        gap_policy="fail", cache_dir=str(tmp_path),
    )

    result, quality, warnings = yb.load_historical_data(
        config,
        start=pd.Timestamp("2025-01-01T00:00:00Z"),
        end=pd.Timestamp("2025-01-02T00:00:00Z"),
    )

    assert calls == ["binance", "bybit"]
    assert len(result) == 24
    assert quality["source"] == "bybit"
    assert quality["missing_candles_estimate"] == 0
    assert quality["unexpected_candles"] == 0
    assert quality["coverage_pct_estimate"] == 100.0
    assert warnings


def test_year_backtest_fail_policy_rejects_wrong_timestamp(tmp_path, monkeypatch):
    from engine import year_backtest as yb

    index = pd.date_range("2025-01-01T00:00:00Z", periods=24, freq="1h")
    frame = pd.DataFrame({
        "timestamp": index,
        "open": np.full(24, 100.0),
        "high": np.full(24, 102.0),
        "low": np.full(24, 99.0),
        "close": np.full(24, 101.0),
        "volume": np.full(24, 10.0),
    })
    frame.loc[23, "timestamp"] = pd.Timestamp("2025-01-01T23:30:00Z")

    monkeypatch.setattr(yb, "_exchange_ohlcv", lambda *args, **kwargs: frame.copy())
    config = yb.BacktestConfig(
        symbol="BTC/USDT", timeframe="1h", days=1,
        exchange_id="binance", data_source="binance",
        gap_policy="fail", cache_dir=str(tmp_path),
    )

    with pytest.raises(RuntimeError, match="complete historical data"):
        yb.load_historical_data(
            config,
            start=pd.Timestamp("2025-01-01T00:00:00Z"),
            end=pd.Timestamp("2025-01-02T00:00:00Z"),
        )
