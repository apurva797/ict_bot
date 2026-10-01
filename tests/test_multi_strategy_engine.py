from unittest.mock import patch
import unittest
from datetime import datetime, timezone

import bot
import pandas as pd
from streamlit.testing.v1 import AppTest
from demo_data import MarketDataResult
from engine.scorer import score_strategies
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MultiStrategyPipelineTests(unittest.TestCase):
    @staticmethod
    def _live_snapshot():
        now = pd.Timestamp.now(tz="UTC")
        bucket = now.floor("5min")
        return {"symbol": "BTC/USDT", "timeframe": "5m", "price": 105.0, "updated_at": now,
                "source": "Test Coinbase snapshot",
                "candle": {"timestamp": bucket, "open": 105.0, "high": 106.0, "low": 104.0,
                           "close": 105.0, "volume": 10.0}}

    def test_bot_risk_plan_keeps_one_point_five_minimum_and_two_r_default(self):
        candles = [[i * 300_000, 100, 102, 98, 100, 10] for i in range(250)]
        for side in ("LONG", "SHORT"):
            with self.subTest(side=side):
                levels = bot.build_multi_strategy_trade_levels(candles, side)
                risk = abs(levels["entry"] - levels["stop"])
                reward = abs(levels["target"] - levels["entry"])
                self.assertEqual(levels["rr"], 2.0)
                self.assertGreaterEqual(reward / risk, 1.5)

    def test_reuses_all_fifteen_existing_signals_and_the_shared_scorer(self):
        expected_names = {
            "TREND", "MOMENTUM", "VOLATILITY", "BREAKOUT", "VWAP", "VOLUME",
            "PRICE_ACTION", "MEAN_REVERSION", "ICT", "WYCKOFF", "KAMA",
            "DONCHIAN", "DIVERGENCE", "CAMBRIDGE_HOOK", "CRYPTO",
        }
        neutral = {"side": "NEUTRAL", "score": 0, "reason": "test neutral"}
        final = {
            "side": "LONG", "score": 61.25, "quality": "NO TRADE",
            "confirmation_points": 3, "heavy_conditions": ["ICT"],
            "confirmation_passed": False, "normal_confirmation": False,
            "heavy_confirmation": False, "active_long": ["TREND", "ICT"],
            "active_short": [], "conditions": [],
        }
        candles = [[i * 300_000, 100, 102, 99, 101, 10] for i in range(250)]
        with patch.object(bot, "detect_regime", return_value={"regime": "RANGE", "score": 70, "reason": "test range"}), \
             patch.object(bot, "get_crypto_signal", return_value=neutral), \
             patch.object(bot, "score_strategies", return_value=final) as scorer:
            # Patch all strategy callables together for the actual invocation.
            with patch.multiple(bot, **{
                attr: lambda _candles, result=neutral: dict(result)
                for attr in (
                    "trend_signal", "momentum_signal", "volatility_signal", "breakout_signal",
                    "vwap_signal", "volume_signal", "price_action_signal", "mean_reversion_signal",
                    "ict_signal", "wyckoff_signal", "kama_signal", "donchian_signal",
                    "divergence_signal", "cambridge_hook_signal",
                )
            }):
                result = bot.analyze_multi_strategy_candles(candles)

        self.assertEqual(set(result["signals"]), expected_names)
        self.assertTrue(all({"side", "score", "reason"} <= signal.keys() for signal in result["signals"].values()))
        self.assertEqual(scorer.call_args.args[0], result["signals"])
        self.assertEqual(scorer.call_args.args[1], "RANGE")
        self.assertEqual(result["final_signal"]["confirmation_points"], 3)
        self.assertFalse(result["final_signal"]["confirmation_passed"])

    def test_scorer_preserves_side_specific_confirmation_for_neutral_conflicts(self):
        result = score_strategies({
            "ICT": {"side": "LONG", "score": 70, "reason": "long ICT"},
            "TREND": {"side": "SHORT", "score": 70, "reason": "short trend"},
        }, "UNKNOWN")
        self.assertEqual(result["side"], "NEUTRAL")
        self.assertEqual(result["long_confirmation_points"], 2)
        self.assertEqual(result["short_confirmation_points"], 1)
        self.assertEqual(result["long_heavy_conditions_list"], ["ICT"])
        self.assertEqual(result["short_heavy_conditions_list"], [])

    def test_streamlit_defaults_to_full_multi_strategy_dashboard(self):
        app = AppTest.from_file(str(ROOT / "app.py")).run()
        self.assertFalse(list(app.exception))
        self.assertEqual(app.radio[0].value, "Multi-Strategy Engine")
        self.assertTrue(any(button.label == "Analyze all strategies" for button in app.button))

    def test_multi_strategy_paper_entry_requires_scorer_confirmation(self):
        index = pd.date_range(datetime(2026, 1, 1, tzinfo=timezone.utc), periods=250, freq="5min")
        close = pd.Series([100 + i * 0.02 for i in range(len(index))], index=index)
        frame = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                              "close": close, "volume": 100.0}, index=index)
        data_result = MarketDataResult(frame, "Test public candles", False)
        analysis = {
            "price": float(close.iloc[-1]),
            "regime": {"regime": "TREND_UP", "score": 90, "reason": "test trend"},
            "signals": {"TREND": {"side": "LONG", "score": 80, "reason": "test trend"}},
            "final_signal": {
                "side": "LONG", "score": 80, "reason": "Confirmed test signal",
                "confirmation_points": 4, "heavy_conditions": [],
                "confirmation_passed": True, "normal_confirmation": True,
                "heavy_confirmation": False,
            },
        }
        with patch("demo_data.fetch_market_data", return_value=data_result), \
             patch("bot.analyze_multi_strategy_candles", return_value=analysis), \
             patch("demo_data.fetch_live_market_snapshot", return_value=self._live_snapshot()):
            app = AppTest.from_file(str(ROOT / "app.py")).run()
            app.selectbox[3].set_value("5m").run()
            next(button for button in app.button if button.label == "Analyze all strategies").click().run()
            next(button for button in app.button if button.label == "Update multi-strategy paper account").click().run()
            app.run()
        self.assertFalse(list(app.exception))
        account = app.session_state["paper_accounts"]["multi.strategy|BTC/USDT|5m"]
        self.assertIsNotNone(account["position"])
        self.assertEqual(account["position"]["side"], "LONG")
        position = account["position"]
        self.assertAlmostEqual(
            (position["target"] - position["entry"]) / position["risk_distance"], 2.0
        )
        self.assertEqual(account["starting_capital"], 10_000.0)
        self.assertEqual(len(app.session_state["paper_accounts"]), 1)

    def test_multi_strategy_paper_entry_stays_blocked_without_confirmation(self):
        index = pd.date_range(datetime(2026, 1, 1, tzinfo=timezone.utc), periods=250, freq="5min")
        close = pd.Series([100 + i * 0.02 for i in range(len(index))], index=index)
        frame = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                              "close": close, "volume": 100.0}, index=index)
        data_result = MarketDataResult(frame, "Test public candles", False)
        analysis = {
            "price": float(close.iloc[-1]),
            "regime": {"regime": "RANGE", "score": 50, "reason": "test range"},
            "signals": {},
            "final_signal": {
                "side": "LONG", "score": 60, "reason": "insufficient confirmations",
                "confirmation_points": 1, "heavy_conditions": [],
                "confirmation_passed": False, "normal_confirmation": False,
                "heavy_confirmation": False,
            },
        }
        with patch("demo_data.fetch_market_data", return_value=data_result), \
             patch("bot.analyze_multi_strategy_candles", return_value=analysis), \
             patch("demo_data.fetch_live_market_snapshot", return_value=self._live_snapshot()):
            app = AppTest.from_file(str(ROOT / "app.py")).run()
            app.selectbox[3].set_value("5m").run()
            next(button for button in app.button if button.label == "Analyze all strategies").click().run()
            next(button for button in app.button if button.label == "Update multi-strategy paper account").click().run()
        self.assertFalse(list(app.exception))
        account = app.session_state["paper_accounts"]["multi.strategy|BTC/USDT|5m"]
        self.assertIsNone(account["position"])
        self.assertTrue(any("TRADE BLOCKED: INSUFFICIENT CONFIRMATION" in item.value for item in app.error))

    def test_streamlit_multi_strategy_action_renders_full_engine_output(self):
        index = pd.date_range(datetime(2026, 1, 1, tzinfo=timezone.utc), periods=250, freq="5min")
        close = pd.Series([100 + i * 0.02 for i in range(len(index))], index=index)
        frame = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                              "close": close, "volume": 100.0}, index=index)
        result = MarketDataResult(frame, "Test public candles", False)
        with patch("demo_data.fetch_market_data", return_value=result), \
             patch("bot.get_crypto_signal", return_value={"side": "NEUTRAL", "score": 0, "reason": "Test data"}), \
             patch("demo_data.fetch_live_market_snapshot", return_value=self._live_snapshot()):
            app = AppTest.from_file(str(ROOT / "app.py")).run()
            next(button for button in app.button if button.label == "Analyze all strategies").click().run()
        self.assertFalse(list(app.exception))
        self.assertTrue(any(metric.label == "Market regime" for metric in app.metric))
        self.assertTrue(any(metric.label == "Final signal" for metric in app.metric))
        self.assertTrue(app.dataframe)


if __name__ == "__main__":
    unittest.main()
