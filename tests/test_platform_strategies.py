import unittest

import numpy as np
import pandas as pd

from demo_backtest import run_backtest
from demo_data import validate_ohlcv
from demo_strategy import EXAMPLES, parse_strategy
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
        self.assertEqual(len(equity), len(frame) - 1)
        self.assertIn("net_pnl", trades)

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


if __name__ == "__main__":
    unittest.main()

