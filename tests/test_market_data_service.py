"""Market data service: provider fallback, normalization, health, and statuses."""

import unittest

import numpy as np
import pandas as pd

from platform_core.errors import PlatformError
from platform_core.market_data import (
    LiveTickerProvider,
    MarketDataService,
    assess_health,
    default_service,
    normalize_ohlcv,
    timeframe_to_ms,
    to_candle_rows,
)
from platform_core.status import DataHealth, Status


def sample_rows(count=150, end=None, freq="5min", base=100.0):
    end = end or pd.Timestamp.now(tz="UTC").floor(freq)
    index = pd.date_range(end=end, periods=count, freq=freq, tz="UTC")
    closes = base + np.arange(count) * 0.5
    return [
        [int(timestamp.value // 1_000_000), open_, high, low, close, 10.0]
        for timestamp, (open_, high, low, close) in zip(
            index, zip(closes - 0.2, closes + 0.5, closes - 0.5, closes)
        )
    ]


class NormalizationTests(unittest.TestCase):
    def test_sorts_duplicates_and_drops_invalid_rows(self):
        rows = sample_rows(150)
        rows[0] = [0, 0, 0, 0, 0, -1]                      # zero prices, negative volume
        rows[5] = [int(pd.Timestamp("2026-01-01 00:05", tz="UTC").value // 1_000_000), 1, 1, 1, 1, 1]
        rows[7] = ["x", "y", "z", "w", "v", "u"]            # non-numeric

        frame, dropped = normalize_ohlcv(rows)

        self.assertGreaterEqual(dropped, 2)
        self.assertTrue(frame.index.is_monotonic_increasing)
        self.assertFalse(frame.index.has_duplicates)
        self.assertFalse(frame.isna().any().any())
        self.assertTrue((frame[["open", "high", "low", "close"]] > 0).all().all())
        self.assertTrue((frame["high"] >= frame[["open", "low", "close"]].max(axis=1)).all())
        self.assertTrue((frame["low"] <= frame[["open", "high", "close"]].min(axis=1)).all())
        self.assertEqual(str(frame.index.tz), "UTC")

    def test_reversed_input_is_sorted_and_never_fabricated(self):
        frame, _ = normalize_ohlcv(list(reversed(sample_rows(60))))
        self.assertTrue(frame.index.is_monotonic_increasing)
        self.assertEqual(len(frame), 60)

    def test_accepts_seconds_milliseconds_and_iso_timestamps(self):
        base = sample_rows(60)
        seconds = [[int(row[0] // 1000), *row[1:]] for row in base]
        iso = [[pd.Timestamp(row[0], unit="ms", tz="UTC").isoformat(), *row[1:]] for row in base]
        for rows in (base, seconds, iso):
            with self.subTest(rows=type(rows[0][0]).__name__):
                frame, _ = normalize_ohlcv(rows)
                self.assertEqual(len(frame), 60)
                self.assertEqual(frame.index[-1].isoformat(),
                                 pd.Timestamp(base[-1][0], unit="ms", tz="UTC").isoformat())

    def test_missing_columns_and_empty_input_are_explicit_errors(self):
        with self.assertRaises(PlatformError) as missing:
            normalize_ohlcv(pd.DataFrame({"open": [1.0], "close": [1.0]}))
        self.assertEqual(missing.exception.status, Status.DATA_UNAVAILABLE)
        self.assertIn("missing required candle fields", missing.exception.message)

        for empty in (None, [], pd.DataFrame()):
            with self.subTest(empty=type(empty).__name__):
                with self.assertRaises(PlatformError):
                    normalize_ohlcv(empty)

    def test_timeframe_conversion_and_unsupported_interval(self):
        self.assertEqual(timeframe_to_ms("5m"), 300_000)
        self.assertEqual(timeframe_to_ms("1D"), 86_400_000)
        with self.assertRaises(PlatformError):
            timeframe_to_ms("7m")


class HealthTests(unittest.TestCase):
    def test_fresh_live_is_connected_and_old_data_is_stale(self):
        fresh, _ = normalize_ohlcv(sample_rows(120))
        stale, _ = normalize_ohlcv(sample_rows(120, end=pd.Timestamp("2020-01-01", tz="UTC")))
        self.assertEqual(assess_health(fresh, "5m", True)[0], DataHealth.CONNECTED)
        self.assertEqual(assess_health(fresh, "5m", False)[0], DataHealth.DEGRADED)
        self.assertEqual(assess_health(stale, "5m", True)[0], DataHealth.STALE)
        self.assertEqual(assess_health(pd.DataFrame(), "5m", True)[0], DataHealth.UNAVAILABLE)


class ServiceFallbackTests(unittest.TestCase):
    def test_primary_success_never_calls_backup(self):
        backup = LiveTickerProvider("backup", lambda s, t, l: sample_rows(l))
        service = MarketDataService([LiveTickerProvider("primary", lambda s, t, l: sample_rows(l)), backup])
        snapshot = service.get_snapshot("BTC/USDT", "5m", 120)
        self.assertEqual(snapshot.source, "primary")
        self.assertTrue(snapshot.is_live)
        self.assertEqual(snapshot.candles, 120)
        self.assertTrue(all(attempt.name == "primary" for attempt in service.stats.attempts))

    def test_provider_exception_falls_back_to_backup(self):
        def offline(_symbol, _timeframe, _limit):
            raise RuntimeError("network down")

        service = MarketDataService([
            LiveTickerProvider("primary", offline),
            LiveTickerProvider("backup", lambda s, t, l: sample_rows(l)),
        ])
        snapshot = service.get_snapshot("BTC/USDT", "5m", 120)
        self.assertEqual(snapshot.source, "backup")
        self.assertFalse(service.stats.attempts[0].ok)
        self.assertTrue(service.stats.attempts[1].ok)

    def test_non_live_sample_is_reported_as_not_live(self):
        service = MarketDataService([
            LiveTickerProvider("sample", lambda s, t, l: sample_rows(l), live=False),
        ])
        snapshot = service.get_snapshot("BTC/USDT", "5m", 120)
        self.assertFalse(snapshot.is_live)
        self.assertEqual(snapshot.health, DataHealth.DEGRADED)
        self.assertIn("Source is not live market data", snapshot.notes)

    def test_all_providers_empty_is_data_unavailable(self):
        service = MarketDataService([
            LiveTickerProvider("a", lambda s, t, l: []),
            LiveTickerProvider("b", lambda s, t, l: None),
        ])
        with self.assertRaises(PlatformError) as raised:
            service.get_snapshot("BTC/USDT", "5m", 120)
        self.assertEqual(raised.exception.status, Status.DATA_UNAVAILABLE)

    def test_too_few_candles_is_insufficient_data_not_unavailable(self):
        service = MarketDataService([LiveTickerProvider("primary", lambda s, t, l: sample_rows(40))])
        with self.assertRaises(PlatformError) as raised:
            service.get_snapshot("BTC/USDT", "5m", 120)
        self.assertEqual(raised.exception.status, Status.INSUFFICIENT_DATA)

    def test_requested_limit_below_minimum_is_rejected(self):
        service = MarketDataService([LiveTickerProvider("primary", lambda s, t, l: sample_rows(l))])
        with self.assertRaises(PlatformError) as raised:
            service.get_snapshot("BTC/USDT", "5m", 50)
        self.assertEqual(raised.exception.status, Status.INSUFFICIENT_DATA)

    def test_price_helpers_use_real_closes(self):
        service = MarketDataService([LiveTickerProvider("primary", lambda s, t, l: sample_rows(l))])
        snapshot = service.get_snapshot("BTC/USDT", "5m", 120)
        self.assertAlmostEqual(service.latest_price(snapshot),
                               float(snapshot.frame["close"].iloc[-1]))
        self.assertGreater(service.price_change_pct(snapshot), 0)
        self.assertEqual(len(to_candle_rows(snapshot.frame)), 120)
        self.assertEqual(len(to_candle_rows(snapshot.frame)[0]), 6)

    def test_default_service_wires_real_providers_in_priority_order(self):
        names = [provider.name for provider in default_service().providers]
        self.assertEqual(names[0], "Binance public OHLCV")
        self.assertEqual(names[1], "Coinbase Exchange public OHLCV")
        self.assertFalse(default_service().providers[-1].live)


if __name__ == "__main__":
    unittest.main()