from unittest.mock import patch

import pandas as pd
import pytest

import demo_data


def _frame(index):
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
        index=index,
    )


def test_merges_compatible_providers_to_exact_expected_index(tmp_path, monkeypatch):
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    end = start + pd.Timedelta(hours=110)
    expected = pd.date_range(start, periods=110, freq="1h")
    monkeypatch.setattr(demo_data, "HISTORICAL_CACHE_DIR", tmp_path)
    with patch.object(demo_data, "_historical_binance", return_value=_frame(expected[:107])), \
         patch.object(demo_data, "_historical_coinbase", return_value=_frame(expected[107:])):
        result = demo_data.fetch_historical_market_data("BTC/USDT", "1h", start, end)
    assert result.frame.index.equals(expected)
    assert len(result.frame) == 110
    assert result.source == "Merged Binance + Coinbase historical OHLCV"


def test_complete_primary_provider_short_circuits_secondary(tmp_path, monkeypatch):
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    end = start + pd.Timedelta(hours=110)
    expected = pd.date_range(start, periods=110, freq="1h")
    monkeypatch.setattr(demo_data, "HISTORICAL_CACHE_DIR", tmp_path)
    with patch.object(demo_data, "_historical_binance", return_value=_frame(expected)), \
         patch.object(demo_data, "_historical_coinbase", side_effect=AssertionError("secondary should not be called")):
        result = demo_data.fetch_historical_market_data("BTC/USDT", "1h", start, end)
    assert result.source == "Binance public historical OHLCV"
    assert not result.used_fallback


def test_unresolved_gaps_fail_closed(tmp_path, monkeypatch):
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    end = start + pd.Timedelta(hours=110)
    expected = pd.date_range(start, periods=110, freq="1h")
    partial = _frame(expected.delete([20, 65]))
    monkeypatch.setattr(demo_data, "HISTORICAL_CACHE_DIR", tmp_path)
    with patch.object(demo_data, "_historical_binance", return_value=partial), \
         patch.object(demo_data, "_historical_coinbase", return_value=partial):
        with pytest.raises(demo_data.MarketDataError, match="Incomplete historical coverage"):
            demo_data.fetch_historical_market_data("BTC/USDT", "1h", start, end)


def test_targeted_gap_recovery_fetches_only_the_missing_data_window(tmp_path, monkeypatch):
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    end = start + pd.Timedelta(hours=110)
    expected = pd.date_range(start, periods=110, freq="1h")
    partial = _frame(expected.delete([20]))
    recovered = _frame(expected[20:21])
    monkeypatch.setattr(demo_data, "HISTORICAL_CACHE_DIR", tmp_path)
    with patch.object(demo_data, "_historical_binance", side_effect=[partial, recovered]), \
         patch.object(demo_data, "_historical_coinbase", return_value=partial):
        result = demo_data.fetch_historical_market_data("BTC/USDT", "1h", start, end)
    assert result.frame.index.equals(expected)
    assert len(result.frame) == 110
    assert result.source == "Merged Binance + Coinbase historical OHLCV"
