import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest

from demo_strategy import evaluate_conditions, interpret_strategy
from voice_strategy import apply_voice_transcript

ROOT = Path(__file__).resolve().parents[1]


class NaturalLanguageStrategyTests(unittest.TestCase):
    def test_english_rsi_entry_and_exit(self):
        result = interpret_strategy("Buy when RSI(14) is below 30 and exit when RSI(14) goes above 70")
        self.assertIsNotNone(result.specification)
        self.assertEqual(result.specification["entry"][0]["indicator"], "RSI")
        self.assertEqual(result.specification["entry"][0]["value"], 30)
        self.assertEqual(result.specification["exit"][0]["value"], 70)

    def test_english_ema_exit_does_not_reverse_the_entry_direction(self):
        result = interpret_strategy("Buy when 20 EMA crosses above 50 EMA and exit when 20 EMA crosses below 50 EMA")
        self.assertEqual(result.specification["side"], "BUY")
        self.assertEqual(result.specification["entry"][0]["period"], 20)
        self.assertEqual(result.specification["entry"][0]["compare_to"]["period"], 50)
        self.assertEqual(result.specification["exit"][0]["operator"], "crosses_below")

    def test_hinglish_ema_crossover_and_market_metadata(self):
        result = interpret_strategy("BTC me EMA 20 50 ko cross kare to buy. BTC 15 minute me.")
        self.assertIsNotNone(result.specification)
        self.assertEqual(result.symbol, "BTC/USDT")
        self.assertEqual(result.timeframe, "15m")
        self.assertEqual(result.specification["entry"][0]["period"], 20)
        self.assertEqual(result.specification["entry"][0]["compare_to"]["period"], 50)

    def test_hindi_rsi_short_input_uses_risk_managed_exit(self):
        result = interpret_strategy("RSI 30 के नीचे buy")
        self.assertIsNotNone(result.specification)
        self.assertEqual(result.specification["entry"][0]["value"], 30)
        self.assertEqual(result.specification["exit"], [])
        frame = pd.DataFrame({"close": np.linspace(100, 110, 50), "high": np.linspace(101, 111, 50),
                              "low": np.linspace(99, 109, 50), "volume": 1.0})
        self.assertFalse(evaluate_conditions(frame, []).any())

    def test_price_above_ema_and_previous_high_breakout(self):
        above = interpret_strategy("Price 200 EMA ke upar ho to long")
        breakout = interpret_strategy("Previous high break ho to buy")
        self.assertEqual(above.specification["entry"][0]["compare_to"], {"indicator": "EMA", "period": 200})
        self.assertEqual(breakout.specification["entry"][0]["compare_to"], {"indicator": "previous_high"})

    def test_vwap_and_rsi_combinations_are_deterministic(self):
        result = interpret_strategy("Buy when price is above VWAP and RSI(14) is above 50")
        self.assertIsNotNone(result.specification)
        self.assertEqual(len(result.specification["entry"]), 2)
        frame = pd.DataFrame({"close": [10.0, 12.0, 9.0], "high": [11.0, 13.0, 10.0],
                              "low": [9.0, 11.0, 8.0], "volume": [2.0, 3.0, 4.0]},
                             index=pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC"))
        self.assertEqual(len(evaluate_conditions(frame, result.specification["entry"])), 3)

    def test_risk_and_target_accept_minimum_and_preserve_default(self):
        result = interpret_strategy("RSI 30 ke neeche buy, 1 percent risk aur 1.5R target")
        self.assertIsNotNone(result.specification)
        self.assertEqual(result.draft["risk_fraction"], 0.01)
        self.assertEqual(result.draft["rr"], 1.5)
        self.assertEqual(result.specification["rr"], 1.5)
        rr_notation = interpret_strategy("RSI 30 ke neeche buy with 1 percent risk and R:R 2")
        self.assertEqual(rr_notation.draft["rr"], 2)
        self.assertEqual(rr_notation.specification["rr"], 2)
        lower_risk = interpret_strategy("RSI 30 ke neeche buy with 0.5 percent risk and 2R target")
        self.assertEqual(lower_risk.specification["risk_fraction"], 0.005)
        self.assertEqual(lower_risk.specification["rr"], 2)

    def test_rr_boundary_accepts_one_point_five_and_higher(self):
        for rr in (1.5, 1.75, 2, 2.5, 3):
            with self.subTest(rr=rr):
                result = interpret_strategy(f"RSI 30 ke neeche buy target {rr}R")
                self.assertIsNotNone(result.specification)
                self.assertEqual(result.specification["rr"], rr)

    def test_rr_below_one_point_five_is_rejected(self):
        for rr in (1.0, 1.25, 1.49):
            with self.subTest(rr=rr):
                result = interpret_strategy(f"RSI 30 ke neeche buy target {rr}R")
                self.assertIsNone(result.specification)
                self.assertIn("below 1.5R", result.validation_message)

    def test_macd_zero_line_is_a_deterministic_supported_condition(self):
        result = interpret_strategy("Buy when MACD crosses above zero")
        self.assertIsNotNone(result.specification)
        condition = result.specification["entry"][0]
        self.assertEqual(condition["indicator"], "MACD")
        self.assertEqual(condition["operator"], "crosses_above")
        self.assertEqual(condition["value"], 0)

    def test_ambiguous_ict_phrase_gets_actionable_clarification(self):
        result = interpret_strategy("Liquidity sweep ke baad short")
        self.assertIsNone(result.specification)
        self.assertIn("Which confirmation", result.clarification)
        self.assertTrue(result.suggestions)

    def test_ict_custom_stop_is_reported_without_rejecting_one_point_five_r(self):
        result = interpret_strategy(
            "BTC 15m me previous high sweep karke bearish reversal ho to short, "
            "SL sweep ke upar aur target 1.5R"
        )
        self.assertEqual(result.symbol, "BTC/USDT")
        self.assertEqual(result.timeframe, "15m")
        self.assertEqual(result.draft["side"], "SELL")
        self.assertEqual(result.draft["rr"], 1.5)
        self.assertIn("custom swing stop", result.validation_message)

    def test_voice_transcript_flows_through_the_same_interpreter(self):
        transcript = apply_voice_transcript("  BTC   me EMA 20 50 ko cross kare to buy. ")
        result = interpret_strategy(transcript)
        self.assertEqual(result.symbol, "BTC/USDT")
        self.assertEqual(result.specification["side"], "BUY")

    def test_streamlit_builder_shows_clarification_instead_of_generic_rejection(self):
        app = AppTest.from_file(str(ROOT / "app.py")).run()
        app.radio[0].set_value("AI Strategy").run()
        next(item for item in app.text_area if item.key == "strategy_text").set_value(
            "Liquidity sweep ke baad short"
        ).run()
        next(item for item in app.button if item.label == "Generate strategy").click().run()
        self.assertFalse(list(app.exception))
        self.assertTrue(any("Which confirmation" in item.value for item in app.info))
        next(item for item in app.button if item.label == "Open Existing ICT Strategy").click().run()
        self.assertFalse(list(app.exception))
        self.assertEqual(app.radio[0].value, "Existing ICT Strategy")

    def test_streamlit_confirmation_displays_requested_one_point_five_r(self):
        app = AppTest.from_file(str(ROOT / "app.py")).run()
        app.radio[0].set_value("AI Strategy").run()
        next(item for item in app.text_area if item.key == "strategy_text").set_value(
            "RSI 30 ke neeche buy target 1.5R"
        ).run()
        next(item for item in app.button if item.label == "Generate strategy").click().run()
        self.assertFalse(list(app.exception))
        target = next(item for item in app.metric if item.label == "Target")
        self.assertEqual(target.value, "1.5R")

    def test_editing_description_invalidates_old_confirmation_before_execution(self):
        app = AppTest.from_file(str(ROOT / "app.py")).run()
        app.radio[0].set_value("AI Strategy").run()
        next(item for item in app.text_area if item.key == "strategy_text").set_value("RSI 30 ke neeche buy").run()
        next(item for item in app.button if item.label == "Generate strategy").click().run()
        self.assertTrue(any(item.label == "Backtest" for item in app.button))
        next(item for item in app.text_area if item.key == "strategy_text").set_value("Price 200 EMA ke upar ho to long").run()
        self.assertTrue(any("description changed" in item.value for item in app.info))
        self.assertFalse(any(item.label == "Backtest" for item in app.button))


if __name__ == "__main__":
    unittest.main()
