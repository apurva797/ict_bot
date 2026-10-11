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
