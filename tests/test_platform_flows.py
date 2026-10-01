"""End-to-end flows: 24/7 paper eligibility, risk, lifecycle, and the real app."""

import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

import demo_data
from demo_paper import advance_ict_paper_account
from platform_core.signals import Signal


ROOT = Path(__file__).resolve().parents[1]


def sample(symbol="BTC/USDT", timeframe="5m", limit=500):
    return demo_data._load_sample_ohlcv(symbol, timeframe, limit)


def paper_state(balance=10_000.0):
    return {
        "balance": balance,
        "starting_capital": balance,
        "position": None,
        "trades": [],
        "last_action": None,
        "market": "BTC/USDT",
        "timeframe": "5m",
        "fee_rate": 0.0004,
    }


def frame_ending_at(timestamp, count=200):
    frame = demo_data._load_sample_ohlcv("BTC/USDT", "5m", count)
    frame = frame.reset_index(drop=True)
    frame.index = pd.date_range(end=pd.Timestamp(timestamp, tz="UTC"),
                                periods=len(frame), freq="5min", tz="UTC")
    return frame


def extend(frame, row):
    new_index = pd.date_range(start=frame.index[-1] + timedelta(minutes=5),
                              periods=1, freq="5min", tz="UTC")
    return pd.concat([frame, pd.DataFrame([row], index=new_index)])


class TwentyFourSevenTests(unittest.TestCase):
    """ICT paper entries depend on setup and risk, never on the clock."""

    def test_every_hour_of_the_day_is_eligible_for_evaluation(self):
        from demo_safety import ict_entry_gate

        for hour in range(24):
            for minute in (0, 17, 33, 59):
                stamp = pd.Timestamp(f"2026-09-30 {hour:02d}:{minute:02d}", tz="UTC")
                allowed, reason = ict_entry_gate(stamp)
                self.assertTrue(allowed, f"blocked at {hour:02d}:{minute:02d} ({reason})")

    def test_a_valid_setup_opens_outside_the_old_kill_zones(self):
        frame = frame_ending_at("2026-09-30 03:00")
        state = paper_state()
        signal = {"side": "LONG", "score": 75, "reason": "sweep + MSS + FVG"}
        with patch("strategies.ict.ict_signal", return_value=signal):
            message = advance_ict_paper_account(frame, state)
        self.assertIsNotNone(state["position"], message)
        self.assertEqual(state["position"]["side"], "LONG")
        self.assertIn("opened", message)

    def test_no_setup_never_forces_a_trade_at_any_hour(self):
        for hour in (0, 3, 8, 13, 20, 23):
            frame = frame_ending_at(f"2026-09-30 {hour:02d}:30")
            state = paper_state()
            neutral = {"side": "NEUTRAL", "score": 20, "reason": "not aligned"}
            with patch("strategies.ict.ict_signal", return_value=neutral):
                advance_ict_paper_account(frame, state)
            self.assertIsNone(state["position"], f"forced a trade at {hour:02d} UTC")
            self.assertEqual(state["trades"], [])

    def test_entries_use_real_atr_levels_and_configured_risk(self):
        from config import RISK_PER_TRADE
        from platform_core.indicators import atr as atr_indicator

        frame = frame_ending_at("2026-09-30 03:00")
        state = paper_state()
        signal = {"side": "LONG", "score": 75, "reason": "valid setup"}
        with patch("strategies.ict.ict_signal", return_value=signal):
            advance_ict_paper_account(frame, state)
        position = state["position"]
        expected_atr = float(atr_indicator(frame, 14).dropna().iloc[-1])
        self.assertAlmostEqual(position["risk_distance"], expected_atr * 1.5, places=6)
        # Not a fabricated flat 1% stop.
        self.assertNotAlmostEqual(position["risk_distance"], position["entry"] * 0.01,
                               places=4)
        self.assertAlmostEqual(position["risk_amount"],
                               state["balance"] * RISK_PER_TRADE, places=6)
        self.assertLessEqual(position["quantity"] * position["entry"], state["balance"])


class RiskIsNeverBypassedTests(unittest.TestCase):
    def test_daily_trade_limit_blocks_the_next_entry(self):
        frame = frame_ending_at("2026-09-30 13:00")
        state = paper_state()
        state["daily_entry_count"] = 3
        with patch("strategies.ict.ict_signal",
                   return_value={"side": "LONG", "score": 80, "reason": "setup"}):
            message = advance_ict_paper_account(frame, state)
        self.assertIsNone(state["position"])
        self.assertIn("daily limit", message.lower())

    def test_news_blackout_blocks_entry_but_is_not_a_time_gate(self):
        frame = frame_ending_at("2026-09-30 13:00")
        state = paper_state()
        with patch("strategies.ict.ict_signal",
                   return_value={"side": "LONG", "score": 80, "reason": "setup"}):
            message = advance_ict_paper_account(frame, state, news_blackout=True)
        self.assertIsNone(state["position"])
        self.assertIn("news blackout", message.lower())

    def test_cooldown_blocks_reentry(self):
        frame = frame_ending_at("2026-09-30 13:00")
        state = paper_state()
        state["last_closed_at"] = frame.index[-1] - timedelta(minutes=5)
        with patch("strategies.ict.ict_signal",
                   return_value={"side": "LONG", "score": 80, "reason": "setup"}):
            message = advance_ict_paper_account(frame, state)
        self.assertIsNone(state["position"])
        self.assertIn("cooldown", message.lower())

    def test_insufficient_candles_returns_a_clear_message_not_a_trade(self):
        state = paper_state()
        message = advance_ict_paper_account(sample(limit=60), state)
        self.assertIsNone(state["position"])
        self.assertIn("Insufficient market data", message)


class PaperLifecycleTests(unittest.TestCase):
    def test_open_then_close_updates_balance_and_history(self):
        frame = frame_ending_at("2026-09-30 13:00")
        state = paper_state()
        with patch("strategies.ict.ict_signal",
                   return_value={"side": "LONG", "score": 80, "reason": "setup"}):
            advance_ict_paper_account(frame, state)
        position = state["position"]
        self.assertIsNotNone(position)
        opening_balance = state["balance"]
        target = position["target"]
        row = {"open": target * 0.999, "high": target * 1.002, "low": target * 0.998,
               "close": target, "volume": 100.0}
        with patch("strategies.ict.ict_signal",
                   return_value={"side": "NEUTRAL", "score": 0, "reason": "no setup"}):
            message = advance_ict_paper_account(extend(frame, row), state)
        self.assertIn("closed", message)
        self.assertIsNone(state["position"])
        self.assertEqual(len(state["trades"]), 1)
        trade = state["trades"][0]
        self.assertEqual(trade["exit_reason"], "TP")
        self.assertEqual(trade["side"], "LONG")
        self.assertGreater(trade["net_pnl"], 0)
        self.assertAlmostEqual(state["balance"], opening_balance + trade["net_pnl"],
                               places=8)
        for field in ("entry", "exit", "stop", "target", "quantity", "fees", "r_multiple"):
            self.assertIn(field, trade)

    def test_stop_loss_closes_with_a_loss(self):
        frame = frame_ending_at("2026-09-30 13:00")
        state = paper_state()
        with patch("strategies.ict.ict_signal",
                   return_value={"side": "LONG", "score": 80, "reason": "setup"}):
            advance_ict_paper_account(frame, state)
        stop = state["position"]["stop"]
        row = {"open": stop * 1.001, "high": stop * 1.002, "low": stop * 0.998,
               "close": stop, "volume": 100.0}
        with patch("strategies.ict.ict_signal",
                   return_value={"side": "NEUTRAL", "score": 0, "reason": "no setup"}):
            advance_ict_paper_account(extend(frame, row), state)
        self.assertIsNone(state["position"])
        self.assertEqual(state["trades"][0]["exit_reason"], "SL")
        self.assertLess(state["trades"][0]["net_pnl"], 0)


class AppFlowTests(unittest.TestCase):
    def run_ict_check(self, result=None):
        result = result or demo_data.MarketDataResult(
            sample(), "Bundled historical sample (not live)", True)
        st.cache_data.clear()
        with patch("demo_data.fetch_market_data", return_value=result):
            app = AppTest.from_file(str(ROOT / "app.py")).run()
            app.radio[0].set_value("ARJUNA Strategy").run()
            next(button for button in app.button
                 if button.label == "Check latest ARJUNA signal").click().run()
        return app

    def test_app_loads_with_paper_banner_and_no_exceptions(self):
        app = AppTest.from_file(str(ROOT / "app.py")).run()
        self.assertFalse(list(app.exception))
        self.assertTrue(any("DEMO MODE: ON" in item.value for item in app.error))
        self.assertTrue(any("PAPER TRADING" in item.value for item in app.markdown))

    def test_ict_signal_check_shows_stages_and_distinguishes_no_setup(self):
        app = self.run_ict_check()
        self.assertFalse(list(app.exception))
        rendered = "\n".join(item.value for item in app.markdown)
        self.assertIn("ICT decision chain", rendered)
        self.assertIn("HTF_BIAS", rendered)
        self.assertIn("LIQUIDITY_SWEEP", rendered)
        # A normal no-setup result is informational, never an error.
        self.assertFalse(any("calculation failed" in item.value.lower()
                             for item in app.error))

    def test_chart_snapshot_uses_the_loaded_market(self):
        app = self.run_ict_check()
        self.assertFalse(list(app.exception))
        snapshot = app.session_state["chart_snapshot"]
        self.assertEqual(snapshot["symbol"], "BTC/USDT")
        self.assertGreater(len(snapshot["frame"]), 0)
        self.assertTrue(any("Data source" in item.value for item in app.markdown))

    def test_indicator_controls_are_available_and_can_be_reset(self):
        app = self.run_ict_check()
        pickers = [item for item in app.multiselect if item.label == "Active indicators"]
        self.assertTrue(pickers, "indicator picker is missing")
        self.assertIn("Exponential Moving Average", pickers[0].options)
        self.assertTrue(any(button.label == "Reset indicators" for button in app.button))


class LiveOrderSafetyTests(unittest.TestCase):
    def test_no_live_execution_path_exists(self):
        from demo_safety import SafetyError
        from platform_core.execution import PaperExecutionEngine

        engine = PaperExecutionEngine()
        with self.assertRaises(SafetyError):
            engine.place_live_order("BTC/USDT", "BUY", 1)

    def test_orders_are_marked_paper_and_carry_no_credentials(self):
        from platform_core.execution import PaperExecutionEngine
        from platform_core.risk import RiskEngine
        from platform_core.settings import RiskConfig

        engine = PaperExecutionEngine(RiskEngine(RiskConfig()))
        signal = Signal(strategy_id="ict", strategy_name="ARJUNA", symbol="BTC/USDT",
                        timeframe="5m", direction="LONG",
                        timestamp=pd.Timestamp("2026-01-01", tz="UTC"),
                        reason="test", entry=100.0, stop_loss=99.0, take_profit=102.0)
        order, decision = engine.submit(signal, {"balance": 10_000.0}, fill_price=100.0)
        self.assertTrue(decision.approved)
        self.assertEqual(order.environment, "PAPER")
        serialized = str(order.to_dict()).lower()
        self.assertNotIn("apikey", serialized)
        self.assertNotIn("secret", serialized)


if __name__ == "__main__":
    unittest.main()
