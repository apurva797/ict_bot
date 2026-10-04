import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from charting import chart_update_kind, make_chart_payload, normalize_chart_ohlcv
from conftest import click, go_to, open_app, open_trade, pick_strategy, rendered, strategy_radio
from demo_data import MarketDataResult

ROOT = Path(__file__).resolve().parents[1]


class ChartNormalizationTests(unittest.TestCase):
    def frame(self, count):
        index = pd.date_range("2026-01-01", periods=count, freq="min", tz="UTC")
        close = [float(100 + i) for i in range(count)]
        frame = pd.DataFrame({
            "open": [value - 0.5 for value in close],
            "high": [value + 1 for value in close],
            "low": [value - 1 for value in close],
            "close": close,
            "volume": 10.0,
        })
        frame.index = index
        return frame

    def test_normalizes_order_duplicates_and_invalid_rows(self):
        frame = self.frame(3).iloc[[2, 0, 1]].copy()
        # Same Unix second as row 0; last valid duplicate should be retained.
        frame.loc[pd.Timestamp("2026-01-01 00:00:00.500", tz="UTC")] = [101, 103, 100, 102, 9]
        frame.loc[pd.Timestamp("2026-01-01 00:03:00", tz="UTC")] = [0, 1, 0, 1, 2]

        normalized = normalize_chart_ohlcv(frame)

        self.assertEqual(str(normalized.index.tz), "UTC")
        self.assertTrue(normalized.index.is_monotonic_increasing)
        self.assertFalse(normalized.index.has_duplicates)
        self.assertEqual(len(normalized), 3)
        self.assertEqual(normalized.iloc[0]["close"], 102)

    def test_chart_payload_scales_for_100_500_and_1000_plus_candles(self):
        for size in (100, 500, 1200):
            with self.subTest(size=size):
                payload = make_chart_payload(self.frame(size), "BTC/USDT · 1m", "BTC/USDT|1m")
                times = [bar["time"] for bar in payload["candles"]]
                self.assertEqual(len(times), size)
                self.assertEqual(times, sorted(set(times)))
                self.assertEqual(payload["candles"][0]["time"] % 60, 0)
                self.assertEqual(len(payload["volumes"]), size)
        self.assertEqual(len(payload["digest"]), 64)

    def test_update_classification_skips_unchanged_data_and_updates_latest(self):
        original = make_chart_payload(self.frame(3), "chart", "BTC/USDT|1m")["candles"]
        self.assertEqual(chart_update_kind(original, list(original)), "unchanged")

        appended = original + [{"time": original[-1]["time"] + 60,
                                "open": 103.5, "high": 105, "low": 103, "close": 104,
                                }]
        self.assertEqual(chart_update_kind(original, appended), "append")

        updated_latest = [*original[:-1], {**original[-1], "close": original[-1]["close"] + 0.25}]
        self.assertEqual(chart_update_kind(original, updated_latest), "latest")

        original_volume = [{"time": bar["time"], "value": 10.0} for bar in original]
        revised_volume = [*original_volume[:-1], {**original_volume[-1], "value": 12.0}]
        self.assertEqual(chart_update_kind(original, original, original_volume, revised_volume), "latest")

        revised_history = [{**original[0], "close": original[0]["close"] + 0.1}, *original[1:]]
        self.assertEqual(chart_update_kind(original, revised_history), "reload")

    def test_chart_survives_repeated_reruns_strategy_switches_and_selector_changes(self):
        market_data = MarketDataResult(
            self.frame(250), "Bundled historical sample (not live)", True,
        )
        with patch("demo_data.fetch_market_data", return_value=market_data):
            app = open_trade(open_app())
            next(item for item in app.selectbox if item.label == "Market").set_value("ETH/USDT").run()
            next(item for item in app.selectbox if item.label == "Candle interval").set_value("15m").run()
            pick_strategy(app, "ARJUNA Strategy")
            click(app, "Check latest ARJUNA signal")
            self.assertFalse(list(app.exception))
            self.assertEqual(app.session_state["chart_snapshot"]["symbol"], "ETH/USDT")
            self.assertEqual(app.session_state["chart_snapshot"]["timeframe"], "15m")
            self.assertIn("ETH/USDT · 15m", rendered(app))

            # Rerun without a chart action, then switch strategy panels. The
            # previously loaded dataset remains at the same stable component slot.
            app.run()
            pick_strategy(app, "Quant Strategy")
            self.assertFalse(list(app.exception))
            self.assertEqual(app.session_state["chart_snapshot"]["symbol"], "ETH/USDT")
            pick_strategy(app, "AI Strategy")
            self.assertFalse(list(app.exception))

            next(item for item in app.selectbox if item.label == "Market").set_value("SOL/USDT").run()
            next(item for item in app.selectbox if item.label == "Candle interval").set_value("5m").run()
            self.assertFalse(list(app.exception))
            # The selectors moved but the chart still shows what was actually
            # loaded, and says so rather than implying the new market is drawn.
            self.assertIn("last loaded market", rendered(app))
            self.assertEqual(app.session_state["chart_snapshot"]["symbol"], "ETH/USDT")
            self.assertEqual(app.session_state["chart_snapshot"]["timeframe"], "15m")


if __name__ == "__main__":
    unittest.main()
