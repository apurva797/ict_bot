import os
import sys
import time
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import pytest
import requests
import streamlit as st
from streamlit.testing.v1 import AppTest

from conftest import click, go_to, open_app, open_trade, pick_strategy, rendered, strategy_radio
import demo_data
from demo_data import MarketDataError, MarketDataResult, fetch_market_data, validate_ohlcv
from demo_safety import DEMO_MODE, LIVE_ORDERS_ENABLED, MAX_LEVERAGE, MAX_RISK_FRACTION, MIN_RISK_REWARD
from strategies.ict import ict_signal


ROOT = Path(__file__).resolve().parents[1]


class LiveSnapshotCacheTests(unittest.TestCase):
    """The UI monitor re-runs on a timer, so snapshots must not block every render."""

    def setUp(self):
        demo_data._SNAPSHOT_CACHE.clear()
        self.addCleanup(demo_data._SNAPSHOT_CACHE.clear)

    @pytest.mark.exercise_live_snapshot
    def test_successful_snapshot_is_reused_within_the_ttl(self):
        payload = {"symbol": "BTC/USDT", "timeframe": "5m", "price": 100.0,
                   "updated_at": pd.Timestamp.now(tz="UTC"),
                   "source": "Coinbase Exchange public market data",
                   "candle": {"close": 100.5}}
        with patch("demo_data.requests.Session") as session:
            demo_data._SNAPSHOT_CACHE[("BTC/USDT", "5m")] = (payload, time.time())
            first = demo_data.fetch_live_market_snapshot("BTC/USDT", "5m")
            second = demo_data.fetch_live_market_snapshot("BTC/USDT", "5m")
        session.assert_not_called()
        self.assertEqual(first["price"], 100.0)
        self.assertEqual(second["price"], 100.0)

    @pytest.mark.exercise_live_snapshot
    def test_cached_snapshot_is_not_shared_by_reference(self):
        payload = {"symbol": "BTC/USDT", "timeframe": "5m", "price": 100.0,
                   "updated_at": pd.Timestamp.now(tz="UTC"),
                   "source": "Coinbase Exchange public market data",
                   "candle": {"close": 100.5}}
        demo_data._SNAPSHOT_CACHE[("BTC/USDT", "5m")] = (payload, time.time())
        first = demo_data.fetch_live_market_snapshot("BTC/USDT", "5m")
        first["candle"]["close"] = -1.0
        second = demo_data.fetch_live_market_snapshot("BTC/USDT", "5m")
        self.assertEqual(second["candle"]["close"], 100.5)

    @pytest.mark.exercise_live_snapshot
    def test_failure_is_cached_without_losing_the_specific_reason(self):
        failure = MarketDataError("Coinbase ticker is stale (900 seconds old).")
        demo_data._SNAPSHOT_CACHE[("BTC/USDT", "5m")] = (failure, time.time())
        with self.assertRaises(MarketDataError) as error:
            demo_data.fetch_live_market_snapshot("BTC/USDT", "5m")
        self.assertIn("stale", str(error.exception))

    @pytest.mark.exercise_live_snapshot
    def test_expired_entry_triggers_a_real_fetch(self):
        demo_data._SNAPSHOT_CACHE[("BTC/USDT", "5m")] = (
            MarketDataError("old failure"), time.time() - demo_data._SNAPSHOT_CACHE_TTL_SECONDS - 1)
        session = Mock()
        session.get.side_effect = OSError("network down")
        with patch("demo_data.requests.Session", return_value=session):
            with self.assertRaises(MarketDataError) as error:
                demo_data.fetch_live_market_snapshot("BTC/USDT", "5m")
        self.assertIn("network down", str(error.exception))
        session.get.assert_called()

    def test_request_timeout_is_bounded(self):
        # A bounded timeout keeps the monitor fragment from freezing the page
        # when the public endpoint is slow.
        self.assertLessEqual(demo_data._SNAPSHOT_REQUEST_TIMEOUT, 5)


class MarketDataFallbackTests(unittest.TestCase):
    def sample_frame(self, symbol="BTC/USDT", timeframe="1h", limit=250):
        return demo_data._load_sample_ohlcv(symbol, timeframe, limit)

    def test_market_data_result_import_and_fetch_return_contract(self):
        self.assertIs(MarketDataResult, demo_data.MarketDataResult)
        frame = self.sample_frame()
        with patch("demo_data._fetch_binance_ohlcv", return_value=frame):
            result = fetch_market_data("BTC/USDT", "1h", 250)

        self.assertIsInstance(result, MarketDataResult)
        self.assertIs(result.frame, frame)
        self.assertEqual(result.source, "Binance public OHLCV")
        self.assertFalse(result.used_fallback)
        self.assertEqual(list(result.frame.columns), ["open", "high", "low", "close", "volume"])
        self.assertEqual(str(result.frame.index.tz), "UTC")

    def test_binance_success_is_selected_without_calling_backup(self):
        frame = self.sample_frame()
        with patch("demo_data._fetch_binance_ohlcv", return_value=frame) as primary, \
             patch("demo_data._fetch_coinbase_ohlcv") as secondary, \
             patch("demo_data._load_sample_ohlcv") as sample:
            result = fetch_market_data("BTC/USDT", "1h", 250)
        primary.assert_called_once()
        secondary.assert_not_called()
        sample.assert_not_called()
        self.assertEqual(result.source, "Binance public OHLCV")
        self.assertFalse(result.used_fallback)

    def test_binance_provider_reaches_the_ccxt_client(self):
        """The provider must resolve ccxt at call time, not via a module global.

        A lazy module attribute alone only covers attribute access, so a
        function body referring to ``ccxt`` directly raises NameError and the
        primary provider silently falls back to the backup.
        """
        frame = self.sample_frame()
        exchange = Mock()
        exchange.fetch_ohlcv.return_value = [
            [int(index.timestamp() * 1000), row.open, row.high, row.low, row.close, row.volume]
            for index, row in frame.iterrows()
        ]
        client = Mock()
        client.binance.return_value = exchange
        with patch.dict(sys.modules, {"ccxt": client}):
            result = demo_data._fetch_binance_ohlcv("BTC/USDT", "1h", 250)
        self.assertEqual(list(result.columns), ["open", "high", "low", "close", "volume"])
        self.assertFalse(result.isna().any().any())

    def test_binance_failure_attempts_coinbase_backup(self):
        frame = self.sample_frame()
        with patch("demo_data._fetch_binance_ohlcv", side_effect=RuntimeError("blocked")), \
             patch("demo_data._fetch_coinbase_ohlcv", return_value=frame) as secondary, \
             patch("demo_data._load_sample_ohlcv") as sample, \
             self.assertLogs("ict_demo.market_data", level="ERROR"):
            result = fetch_market_data("BTC/USDT", "1h", 250)
        secondary.assert_called_once()
        sample.assert_not_called()
        self.assertEqual(result.source, "Coinbase Exchange public OHLCV")
        self.assertTrue(result.used_fallback)

    def test_both_live_providers_failing_selects_matching_bundled_csv(self):
        with patch("demo_data._fetch_binance_ohlcv", side_effect=RuntimeError("blocked")), \
             patch("demo_data._fetch_coinbase_ohlcv", side_effect=RuntimeError("blocked")), \
             self.assertLogs("ict_demo.market_data", level="ERROR"):
            result = fetch_market_data("BTC/USDT", "1h", 1000)
        self.assertEqual(result.source, "Bundled historical sample (not live)")
        self.assertTrue(result.used_fallback)
        self.assertGreaterEqual(len(result.frame), 100)

    def test_coinbase_http_errors_timeouts_and_malformed_payload_fall_back(self):
        cases = (403, 429, 451, "timeout", "malformed")
        for failure in cases:
            with self.subTest(failure=failure):
                session = Mock()
                if failure == "timeout":
                    session.get.side_effect = requests.Timeout("timeout")
                else:
                    response = Mock()
                    response.raise_for_status.side_effect = (
                        requests.HTTPError(f"HTTP {failure}") if isinstance(failure, int) else None
                    )
                    response.json.return_value = {"unexpected": "payload"} if failure == "malformed" else []
                    session.get.return_value = response
                with patch("demo_data._fetch_binance_ohlcv", side_effect=RuntimeError("blocked")), \
                     patch("demo_data.requests.Session", return_value=session), \
                     self.assertLogs("ict_demo.market_data", level="ERROR"):
                    result = fetch_market_data("BTC/USDT", "1h", 250)
                self.assertEqual(result.source, "Bundled historical sample (not live)")

    def test_bundled_samples_are_valid_for_each_market_and_interval(self):
        for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
            for timeframe in ("1h", "15m", "5m"):
                with self.subTest(symbol=symbol, timeframe=timeframe):
                    frame = self.sample_frame(symbol, timeframe)
                    self.assertGreaterEqual(len(frame), 100)
                    self.assertIsInstance(frame.index, pd.DatetimeIndex)
                    self.assertEqual(str(frame.index.tz), "UTC")
                    self.assertTrue(frame.index.is_monotonic_increasing)
                    self.assertTrue((frame.high >= frame[["open", "low", "close"]].max(axis=1)).all())

    def test_existing_ict_logic_accepts_bundled_sample_candles(self):
        frame = self.sample_frame("BTC/USDT", "1h", 250)
        candles = [
            [int(timestamp.timestamp() * 1000), row.open, row.high, row.low, row.close, row.volume]
            for timestamp, row in frame.iterrows()
        ]
        signal = ict_signal(candles)
        self.assertIn(signal["side"], {"LONG", "SHORT", "NEUTRAL"})
        self.assertGreaterEqual(signal["score"], 0)
        self.assertIn("reason", signal)

    def test_ui_shows_backup_notice_and_active_source_after_readonly_ict_check(self):
        result = MarketDataResult(self.sample_frame(), "Bundled historical sample (not live)", True)
        st.cache_data.clear()
        with patch("demo_data.fetch_market_data", return_value=result):
            app = open_app()
            pick_strategy(app, "ARJUNA Strategy")
            click(app, "Check latest ARJUNA signal")
        self.assertFalse(list(app.exception))
        self.assertIn("Using backup data source", [item.value for item in app.info])
        self.assertTrue(any("Bundled historical sample (not live)" in item.value for item in app.caption))
        # The simulation-only banner is always on screen, so a user can never
        # mistake backup candles for a live, tradable feed.
        self.assertIn("PAPER TRADING", rendered(app))

    def test_quant_strategy_runs_through_streamlit_backtest_flow(self):
        result = MarketDataResult(self.sample_frame(limit=500), "Bundled historical sample (not live)", True)
        st.cache_data.clear()
        with patch("demo_data.fetch_market_data", return_value=result):
            app = open_app()
            pick_strategy(app, "Quant Strategy")
            click(app, "Backtest Quant")
        self.assertFalse(list(app.exception))
        self.assertTrue(any("Quant backtest" in item.value for item in app.subheader))
        self.assertIn("Using backup data source", [item.value for item in app.info])

    def test_custom_strategy_review_code_and_version_workflow(self):
        result = MarketDataResult(self.sample_frame(limit=500), "Bundled historical sample (not live)", True)
        st.cache_data.clear()
        with patch("demo_data.fetch_market_data", return_value=result):
            app = open_app()
            pick_strategy(app, "AI Strategy")
            click(app, "Example 1")
            # Choosing an example only fills the description; interpreting it is
            # an explicit second step so nothing runs without being asked.
            click(app, "Generate strategy")
            rules = next(item for item in app.text_area if item.key == "strategy_rules_json")
            rules.set_value(
                '{"side":"BUY","entry":[{"indicator":"RSI","period":14,"operator":"<","value":25}],'
                '"exit":[{"indicator":"RSI","period":14,"operator":">","value":70}]}'
            ).run()
            click(app, "Backtest")
            self.assertEqual(app.session_state["generated_strategy"]["entry"][0]["value"], 25.0)
            click(app, "Generate code preview")
            self.assertIn("evaluate_strategy", app.session_state["generated_code"])
            self.assertIn("exits = evaluate_conditions", app.session_state["generated_code"])
            click(app, "Save strategy version")
            self.assertEqual(app.session_state["strategy_library"][0]["version"], 1)
        self.assertFalse(list(app.exception))

    def test_no_provider_failure_crashes_the_streamlit_ict_flow(self):
        st.cache_data.clear()
        with patch("demo_data._fetch_binance_ohlcv", side_effect=RuntimeError("blocked")), \
             patch("demo_data._fetch_coinbase_ohlcv", side_effect=RuntimeError("offline")), \
             self.assertLogs("ict_demo.market_data", level="ERROR"):
            app = open_app()
            pick_strategy(app, "ARJUNA Strategy")
            click(app, "Check latest ARJUNA signal")
        self.assertFalse(list(app.exception))
        self.assertIn("Using backup data source", [item.value for item in app.info])
        self.assertTrue(any("Bundled historical sample (not live)" in item.value for item in app.caption))

    def test_demo_risk_controls_are_unchanged_and_live_orders_disabled(self):
        self.assertTrue(DEMO_MODE)
        self.assertFalse(LIVE_ORDERS_ENABLED)
        self.assertEqual(MAX_RISK_FRACTION, 0.01)
        self.assertEqual(MIN_RISK_REWARD, 1.5)
        self.assertEqual(MAX_LEVERAGE, 1.0)

    def test_streamlit_app_has_no_broker_credential_inputs(self):
        app = open_app()
        labels = [item.label.lower() for item in [*app.text_input, *app.text_area]]
        self.assertFalse(any(any(word in label for word in ("broker", "api key", "credential", "password")) for label in labels))
        page = rendered(app)
        self.assertIn("PAPER TRADING", page)
        self.assertIn("Simulation only", page)

    def test_public_data_and_sample_fallback_require_no_credentials(self):
        frame = self.sample_frame()
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False), \
             patch("demo_data._fetch_binance_ohlcv", side_effect=RuntimeError("offline")), \
             patch("demo_data._fetch_coinbase_ohlcv", return_value=frame), \
             self.assertLogs("ict_demo.market_data", level="ERROR"):
            result = fetch_market_data("BTC/USDT", "1h", 250)
            self.assertEqual(os.environ.get("OPENAI_API_KEY"), "")
        self.assertEqual(len(result.frame), len(frame))

    def test_unsupported_interval_returns_a_safe_data_error(self):
        with self.assertRaises(MarketDataError):
            fetch_market_data("BTC/USDT", "2m", 250)


if __name__ == "__main__":
    unittest.main()

