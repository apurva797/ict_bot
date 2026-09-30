import ast
import pathlib
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from demo_backtest import run_backtest, run_ict_backtest
from demo_data import MarketDataError, validate_ohlcv
from demo_paper import advance_ict_paper_account, advance_paper_account
from engine.paper import PaperTrader
from demo_safety import SafetyError, ict_entry_gate, reject_live_order, validate_risk_controls
from demo_strategy import EXAMPLES, StrategyError, parse_strategy, parse_strategy_json, validate_strategy


class DemoSafetyTests(unittest.TestCase):
    def candles(self, n=500):
        x = np.arange(n)
        close = 100 + 8 * np.sin(x / 7) + 0.02 * x
        rows = []
        for i, c in enumerate(close):
            rows.append([1700000000000 + i * 3600000, c, c * 1.01, c * 0.99, c, 100])
        return validate_ohlcv(rows)

    def test_exactly_three_examples_parse_and_validate(self):
        self.assertEqual(len(EXAMPLES), 3)
        for example in EXAMPLES:
            self.assertEqual(parse_strategy(example)["side"], "BUY")

    def test_invalid_json_and_unsupported_indicator_rejected(self):
        with self.assertRaises(StrategyError):
            parse_strategy_json('{"entry": [}')
        with self.assertRaises(StrategyError):
            validate_strategy({"entry": [{"indicator": "__import__", "operator": ">", "value": 0}], "exit": [{"indicator": "price", "operator": ">", "value": 0}]})
        with self.assertRaises(StrategyError):
            validate_strategy({"entry": [], "exit": []})

    def test_risk_rr_and_leverage_limits(self):
        for values in ({"risk_fraction": .011}, {"rr": 1.99}, {"leverage": 1.01}):
            with self.assertRaises(StrategyError):
                validate_strategy({"entry": [{"indicator": "price", "operator": ">", "value": 1}], "exit": [{"indicator": "price", "operator": ">", "value": 1}], **values})
        for args in ((.011, 2, 1), (.01, 1.99, 1), (.01, 2, 1.01)):
            with self.assertRaises(SafetyError):
                validate_risk_controls(*args)

    def test_live_orders_hard_blocked(self):
        with self.assertRaises(SafetyError):
            reject_live_order("BTC/USDT", "BUY")

    def test_existing_paper_engine_enforces_risk_rr_and_leverage(self):
        trader = PaperTrader(starting_balance=10_000)
        self.assertFalse(trader.open_position("LONG", 100, 99, 101, risk_amount=100, quantity=50))
        self.assertFalse(trader.open_position("LONG", 100, 99, 101.99, risk_amount=100, quantity=50))
        self.assertFalse(trader.open_position("LONG", 100, 99, 102, risk_amount=101, quantity=50))
        self.assertFalse(trader.open_position("LONG", 100, 99, 102, risk_amount=100, quantity=101))
        self.assertTrue(trader.open_position("LONG", 100, 99, 102, risk_amount=100, quantity=50))
        self.assertLessEqual(trader.position["notional"], trader.balance)

    def test_ai_output_has_no_code_or_tool_fields(self):
        strategy = parse_strategy(EXAMPLES[0])
        self.assertNotIn("python", str(strategy).lower())
        self.assertNotIn("broker", strategy)
        self.assertNotIn("__import__", str(strategy))
        for filename in ("demo_strategy.py", "app.py", "demo_paper.py", "demo_backtest.py"):
            tree = ast.parse(pathlib.Path(filename).read_text(encoding="utf-8"))
            names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            self.assertFalse({"eval", "exec"} & names)
            attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            self.assertFalse({"create_order", "create_market_order", "create_limit_order"} & attributes)

    def test_bad_data_and_ict_gates_block(self):
        with self.assertRaises(MarketDataError):
            validate_ohlcv([[1, 1, 2, 1, 1, 1]] * 5)
        with self.assertRaises(MarketDataError):
            validate_ohlcv([[1, 1, 2, 0, 1, 4]] * 40)
        good, _ = ict_entry_gate(pd.Timestamp("2026-09-30 08:00", tz="UTC"), news_blackout=False)
        self.assertTrue(good)
        self.assertFalse(ict_entry_gate(pd.Timestamp("2026-09-30 08:00", tz="UTC"), news_blackout=True)[0])
        self.assertTrue(ict_entry_gate(pd.Timestamp("2026-09-30 11:00", tz="UTC"))[0])
        self.assertTrue(ict_entry_gate(pd.Timestamp("2026-09-30 00:00", tz="UTC"))[0])
        self.assertTrue(ict_entry_gate(pd.Timestamp("2026-09-30 23:59", tz="UTC"))[0])
        t = pd.Timestamp("2026-09-30 08:00", tz="UTC")
        self.assertFalse(ict_entry_gate(t + pd.Timedelta(minutes=10), last_trade_at=t)[0])

    def test_valid_ict_setup_outside_kill_zones_can_open_paper_entry(self):
        frame = self.candles(120)
        frame.index = pd.date_range(
            end=pd.Timestamp("2026-09-30 03:30", tz="UTC"),
            periods=len(frame),
            freq="5min",
        )
        state = {"balance": 10_000.0, "position": None, "trades": [], "last_action": None}
        with patch(
            "strategies.ict.ict_signal",
            return_value={"side": "LONG", "score": 75, "reason": "valid ICT setup"},
        ):
            message = advance_ict_paper_account(frame, state, news_blackout=False)

        self.assertIsNotNone(state["position"])
        self.assertEqual(state["position"]["side"], "LONG")
        self.assertIn("paper position opened", message)

    def test_no_valid_ict_setup_does_not_force_a_paper_entry(self):
        frame = self.candles(120)
        frame.index = pd.date_range(
            end=pd.Timestamp("2026-09-30 03:30", tz="UTC"),
            periods=len(frame),
            freq="5min",
        )
        state = {"balance": 10_000.0, "position": None, "trades": [], "last_action": None}
        with patch(
            "strategies.ict.ict_signal",
            return_value={"side": "NEUTRAL", "score": 20, "reason": "no aligned setup"},
        ):
            message = advance_ict_paper_account(frame, state, news_blackout=False)

        self.assertIsNone(state["position"])
        self.assertEqual(message, "No ICT paper entry: No valid ICT setup.")

    def test_paper_cooldown_prevents_reentry(self):
        rows = []
        now = pd.Timestamp("2026-09-30 08:00", tz="UTC")
        for i in range(40):
            close = 100.0 if i < 39 else 97.0
            rows.append([int((now - pd.Timedelta(hours=39-i)).timestamp()*1000), close, close*1.001, close*.999, close, 100])
        frame = validate_ohlcv(rows)
        state = {"balance": 10_000, "position": None, "trades": [], "last_action": None,
                 "last_closed_at": frame.index[-1] - pd.Timedelta(minutes=10)}
        message = advance_paper_account(frame, parse_strategy(EXAMPLES[2]), state)
        self.assertIsNone(state["position"])
        self.assertEqual(state["trades"], [])

    def test_paper_trade_opens_and_closes_in_memory(self):
        start = pd.Timestamp("2026-09-30 08:00", tz="UTC")
        rows = []
        for i in range(40):
            close = 100.0 if i < 39 else 97.0
            rows.append([int((start - pd.Timedelta(hours=39-i)).timestamp()*1000), close, close*1.001, close*.999, close, 100])
        frame = validate_ohlcv(rows)
        strategy = parse_strategy(EXAMPLES[2])
        state = {"balance": 10_000, "position": None, "trades": [], "last_action": None}
        advance_paper_account(frame, strategy, state)
        self.assertIsNotNone(state["position"])
        self.assertLessEqual(state["position"]["quantity"] * state["position"]["entry"], state["balance"])
        close = state["position"]["target"] * 1.001
        row = [int((frame.index[-1] + pd.Timedelta(hours=1)).timestamp()*1000), close, close*1.001, close*.999, close, 100]
        later = validate_ohlcv(rows + [row])
        advance_paper_account(later, strategy, state)
        self.assertIsNone(state["position"])
        self.assertEqual(len(state["trades"]), 1)

    def test_backtest_produces_metrics_equity_and_history(self):
        for example in EXAMPLES:
            metrics, equity, trades = run_backtest(self.candles(), parse_strategy(example))
            self.assertIn("Maximum drawdown %", metrics)
            self.assertGreater(len(equity), 0)
            self.assertIn("net_pnl", trades.columns)

    def test_existing_ict_backtest_is_simulation_only(self):
        metrics, equity, trades = run_ict_backtest(self.candles(120))
        self.assertIn("Number of trades", metrics)
        self.assertEqual(len(equity), 119)
        self.assertIn("net_pnl", trades.columns)


if __name__ == "__main__":
    unittest.main()

