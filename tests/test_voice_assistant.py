"""Focused tests for safe voice-assistant parsing and factual replies."""

import unittest
from unittest.mock import patch

import pandas as pd

from ui.voice_assistant import (StrategyAwareVoiceContext, build_context,
                                parse_intent, _reply)


class VoiceAssistantTests(unittest.TestCase):
    def test_hinglish_market_request_is_normalized(self):
        intent = parse_intent("Bhai BTC ka analysis kar")
        self.assertEqual(intent.action, "explain")
        self.assertEqual(intent.symbol, "BTC/USDT")
        self.assertEqual(intent.language, "hinglish")

    def test_safe_navigation_intents_are_bounded(self):
        self.assertEqual(parse_intent("open portfolio").action, "portfolio")
        self.assertEqual(parse_intent("Research Hub kholo").action, "research")
        self.assertEqual(parse_intent("set chart to 1h").action, "timeframe")
        self.assertEqual(parse_intent("run arbitrary python").action, "unknown")

    def test_market_reply_uses_loaded_snapshot_only(self):
        frame = pd.DataFrame({"close": [100.0, 102.0]})
        with patch("ui.voice_assistant.st.session_state", {
            "shell_symbol": "BTC/USDT",
            "chart_snapshot": {"symbol": "BTC/USDT", "frame": frame},
        }):
            reply = _reply(parse_intent("analyse BTC"), "Trade")
        self.assertIn("+2.00%", reply)
        self.assertIn("not a forecast", reply)

    def test_missing_snapshot_is_explicit(self):
        with patch("ui.voice_assistant.st.session_state", {"shell_symbol": "ETH/USDT"}):
            reply = _reply(parse_intent("analyse ETH"), "Trade")
        self.assertIn("not loaded", reply)

    def test_context_is_strategy_aware_without_secrets(self):
        with patch("ui.voice_assistant.st.session_state", {
            "strategy_choice": "Quant Strategy",
            "shell_symbol": "ETH/USDT",
            "shell_timeframe": "15m",
            "research_multi_strategy_backtest": {
                "backtest_config": {"start": "2026-01-01"},
                "metrics": {"Max drawdown %": 4.0},
            },
        }):
            context = build_context("Trade")
        self.assertEqual(context, StrategyAwareVoiceContext(
            strategy="Quant", symbol="ETH/USDT", timeframe="15m",
            screen="Trade", backtest_configuration={"start": "2026-01-01"},
            visible_metrics={"Max drawdown %": 4.0},
        ))
