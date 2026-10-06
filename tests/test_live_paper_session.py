import unittest
from unittest.mock import patch
from types import SimpleNamespace

import demo_data
import pandas as pd
from conftest import click, go_to, open_app, rendered
from ui.screens.trade import _run_live_paper_tick


class LivePaperSessionTests(unittest.TestCase):
    def test_start_and_stop_use_fresh_non_sample_data(self):
        frame = demo_data._load_sample_ohlcv("BTC/USDT", "1h", 500)
        live_result = demo_data.MarketDataResult(frame, "Test live provider", False)
        with patch("demo_data.fetch_market_data", return_value=live_result):
            app = go_to(open_app(), "Trade")
            app = click(app, "Start paper session")
            self.assertFalse(list(app.exception))
            self.assertTrue(app.session_state["live_paper_session"]["active"])
            self.assertEqual(app.session_state["live_paper_session"]["last_source"],
                             "Test live provider")
            self.assertIn("PAPER ENGINE RUNNING", rendered(app))
            app = click(app, "Stop paper session")
            self.assertFalse(app.session_state["live_paper_session"]["active"])

    def test_sample_data_cannot_claim_a_running_live_session(self):
        frame = demo_data._load_sample_ohlcv("BTC/USDT", "1h", 500)
        sample_result = demo_data.MarketDataResult(
            frame, "Bundled historical sample (not live)", True)
        with patch("demo_data.fetch_market_data", return_value=sample_result):
            app = go_to(open_app(), "Trade")
            app = click(app, "Start paper session")
            self.assertFalse(app.session_state["live_paper_session"]["active"])
            self.assertIn("Live paper session stopped", app.session_state["live_paper_session"]["last_error"])

    def test_provider_to_quant_strategy_to_paper_pnl_boundary(self):
        first = demo_data._load_sample_ohlcv("BTC/USDT", "1h", 500).tail(60).copy()
        first.index = pd.date_range("2026-10-01", periods=len(first), freq="h", tz="UTC")
        second = first.copy()
        second.index = pd.date_range("2026-10-02", periods=len(second), freq="h", tz="UTC")
        second.iloc[-1, second.columns.get_loc("close")] *= 1.01
        second.iloc[-1, second.columns.get_loc("high")] = second.iloc[-1]["close"] * 1.001
        second.iloc[-1, second.columns.get_loc("low")] = second.iloc[-1]["close"] * 0.999
        results = [
            demo_data.MarketDataResult(first, "Deterministic integration provider", False),
            demo_data.MarketDataResult(second, "Deterministic integration provider", False),
        ]
        state = {"live_paper_session": {
            "active": True, "strategy": "Quant Strategy",
            "symbol": "BTC/USDT", "timeframe": "1h",
        }}
        calls = {"count": 0}
        def signals(frame, params):
            calls["count"] += 1
            result = pd.Series("NEUTRAL", index=frame.index)
            result.iloc[-1] = "BUY" if calls["count"] == 1 else "SELL"
            return result
        fake_plugin = SimpleNamespace(signal_series=signals)
        with patch("ui.screens.trade.st.session_state", state), \
             patch("ui.screens.trade.marketdata.demo_data.fetch_market_data",
                   side_effect=results), \
             patch("platform_strategies.strategy_registry.get", return_value=fake_plugin):
            _run_live_paper_tick("BTC/USDT", "1h", 10_000)
            account = state["paper_accounts"]["quant.trend|BTC/USDT|1h"]
            self.assertIsNotNone(account["position"])
            opened_mark = account["last_mark"]
            _run_live_paper_tick("BTC/USDT", "1h", 10_000)
        self.assertNotEqual(account["last_mark"], opened_mark)
        self.assertIsNone(account["position"])
        self.assertEqual(len(account["trades"]), 1)
