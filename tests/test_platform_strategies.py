import unittest
from types import ModuleType
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from demo_backtest import run_backtest
from demo_data import validate_ohlcv
from demo_paper import advance_paper_account
from demo_strategy import EXAMPLES, generate_strategy_code, parse_strategy, validate_strategy
from gemini_service import generate_strategy_json
from portfolio import portfolio_snapshot
from platform_strategies import (
    CustomDslStrategyPlugin,
    StrategyMetadata,
    StrategyRegistry,
    latest_signal,
    strategy_registry,
)


class StrategyPlatformTests(unittest.TestCase):
    def candles(self, n=240):
        rows = []
        start = pd.Timestamp("2026-01-01", tz="UTC")
        for i in range(n):
            close = 100 + 0.03 * i + 4 * np.sin(i / 8)
            rows.append([int((start + pd.Timedelta(hours=i)).timestamp() * 1000), close,
                         close * 1.01, close * .99, close, 100 + i])
        return validate_ohlcv(rows)

    def test_registry_exposes_ict_quant_and_custom_plugins(self):
        ids = {item.id for item in strategy_registry.list()}
        self.assertTrue({"ict", "quant.trend", "quant.mean_reversion", "custom.dsl"} <= ids)

    def test_registry_rejects_duplicate_ids(self):
        registry = StrategyRegistry()

        class Plugin:
            metadata = StrategyMetadata("same", "name", "description", "test")

            def signal_series(self, frame, parameters=None):
                return pd.Series(None, index=frame.index, dtype="object")

        registry.register(Plugin())
        with self.assertRaises(ValueError):
            registry.register(Plugin())

    def test_quant_plugins_return_index_aligned_directional_series(self):
        frame = self.candles()
        trend = strategy_registry.get("quant.trend").signal_series(frame, {"fast": 8, "slow": 21})
        mean_reversion = strategy_registry.get("quant.mean_reversion").signal_series(
            frame, {"period": 14, "oversold": 35, "overbought": 65}
        )
        for values in (trend, mean_reversion):
            self.assertTrue(values.index.equals(frame.index))
            self.assertLessEqual(set(values.dropna()), {"BUY", "SELL"})
        metrics, equity, trades = run_backtest(
            frame,
            {"side": "BUY", "entry": [{"indicator": "price", "operator": ">", "value": 0}],
             "exit": [{"indicator": "price", "operator": "<", "value": 0}]},
            signal_sides=trend,
        )
        self.assertIn("Total P&L", metrics)
        self.assertIn("Expectancy", metrics)
        self.assertIn("Fees paid", metrics)
        self.assertIn("Estimated slippage paid", metrics)
        self.assertIn("Average R", metrics)
        self.assertEqual(len(equity), len(frame) - 1)
        self.assertIn("net_pnl", trades)

    def test_quant_signal_uses_shared_demo_paper_engine(self):
        frame = self.candles(50)
        signals = pd.Series(None, index=frame.index, dtype="object")
        signals.iloc[-1] = "BUY"
        state = {"balance": 10_000.0, "position": None, "trades": [], "last_action": None}
        strategy = {"side": "BUY", "entry": [], "exit": [], "risk_fraction": .01, "rr": 2.0, "leverage": 1.0}
        message = advance_paper_account(frame, strategy, state, signal_sides=signals)
        self.assertIn("Paper position opened", message)
        self.assertEqual(state["position"]["side"], "LONG")

    def test_portfolio_sums_realized_and_marked_session_accounts(self):
        accounts = [
            {"starting_capital": 1000, "balance": 1050, "position": None,
             "trades": [{"net_pnl": 50}]},
            {"starting_capital": 500, "balance": 480, "last_mark": 102,
             "position": {"side": "LONG", "entry": 100, "quantity": 2}, "trades": []},
        ]
        result = portfolio_snapshot(accounts)
        self.assertEqual(result["balance"], 1530)
        self.assertEqual(result["realized_pnl"], 30)
        self.assertEqual(result["unrealized_pnl"], 4)
        self.assertEqual(result["equity"], 1534)
        self.assertEqual(result["positions"], 1)
        self.assertEqual(result["closed_trades"], 1)

    def test_rule_signals_are_causal_and_latest_signal_is_explained(self):
        frame = self.candles()
        spec = parse_strategy(EXAMPLES[1])
        plugin = CustomDslStrategyPlugin()
        complete = plugin.signal_series(frame, spec)
        prefix = plugin.signal_series(frame.iloc[:-1], spec)
        self.assertTrue(complete.iloc[:-1].equals(prefix))
        result = latest_signal(plugin, frame, spec)
        if result is not None:
            self.assertIn(result.side, {"BUY", "SELL"})
            self.assertEqual(result.timestamp, frame.index[-1])
            self.assertTrue(result.explanation)

    def test_gemini_uses_server_key_and_schema_constrained_json(self):
        google = ModuleType("google")
        genai = ModuleType("google.genai")
        types = ModuleType("google.genai.types")
        response = Mock(text='{"side":"BUY","entry":[],"exit":[]}')
        client = Mock()
        client.models.generate_content.return_value = response
        genai.Client = Mock(return_value=client)
        types.GenerateContentConfig = lambda **kwargs: kwargs
        google.genai = genai
        with patch.dict("os.environ", {"GEMINI_API_KEY": "server-only-test-key", "GEMINI_MODEL": "test-model"}), \
             patch.dict("sys.modules", {"google": google, "google.genai": genai, "google.genai.types": types}):
            result = generate_strategy_json("Buy when RSI is below 30")
        self.assertEqual(result, response.text)
        kwargs = client.models.generate_content.call_args.kwargs
        self.assertEqual(kwargs["model"], "test-model")
        self.assertEqual(kwargs["config"]["response_mime_type"], "application/json")
        self.assertNotIn("server-only-test-key", str(kwargs))

    def test_local_parser_understands_ema_cross_with_rsi_filter(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "", "OPENAI_API_KEY": ""}):
            parsed = parse_strategy("BTC 15m: EMA 20 crosses above EMA 50 and RSI > 55")
        self.assertEqual(len(parsed["entry"]), 2)
        self.assertEqual(parsed["entry"][1]["indicator"], "RSI")
        self.assertEqual(parsed["entry"][1]["value"], 55.0)

    def test_code_generator_returns_validated_deterministic_source_only(self):
        spec = parse_strategy(EXAMPLES[1])
        generated = generate_strategy_code(spec)
        compile(generated, "generated_strategy.py", "exec")
        self.assertIn("DSL version 1.0", generated)
        self.assertNotIn("exec(", generated)
        self.assertNotIn("eval(", generated)
        self.assertEqual(generated, generate_strategy_code(validate_strategy(spec)))


if __name__ == "__main__":
    unittest.main()

