"""Comprehensive automated tests for Institutional Backtesting and Daily Returns.

Validates:
- Historical-data validation and integrity
- Exact calendar daily return and daily P&L calculations
- Mathematical equity reconciliation: sum(daily_pnl) == net_pnl
- Realistic transaction costs (fees and slippage)
- Position sizing and 1% risk management limits
- Conservative same-candle stop-loss priority
- Lookahead-bias prevention (future candle invariance)
- Sharpe and Sortino ratio edge cases
- Empty datasets and insufficient candle handling
- Reproducibility
- Chronological out-of-sample partitioning
"""

import math
import unittest
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd

from demo_data import MarketDataError, validate_ohlcv
from engine.daily_returns import compute_daily_returns_table
from engine.backtest_engine import InstitutionalBacktester, run_one_year_backtest


def _synthetic_ohlcv(
    periods: int = 250,
    start_price: float = 100.0,
    freq: str = "1h",
    volatility: float = 0.5,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate deterministic synthetic candles for unit tests."""
    rng = np.random.RandomState(seed)
    start_dt = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
    index = pd.date_range(start_dt, periods=periods, freq=freq)
    
    prices = [start_price]
    for _ in range(periods - 1):
        change = rng.normal(0.05, volatility)
        prices.append(max(10.0, prices[-1] + change))
        
    records = []
    for p in prices:
        spread = rng.uniform(0.1, 0.4)
        open_ = p
        close_ = p + rng.normal(0, 0.2)
        high_ = max(open_, close_) + spread
        low_ = min(open_, close_) - spread
        volume = rng.uniform(10.0, 100.0)
        records.append({
            "open": open_,
            "high": high_,
            "low": low_,
            "close": close_,
            "volume": volume,
        })
    return pd.DataFrame(records, index=index)


class TestInstitutionalBacktest(unittest.TestCase):
    """Institutional backtesting validation test suite."""

    def test_historical_data_validation(self):
        """Test OHLCV validation rules: strictly monotonic, positive, valid bounds."""
        valid_rows = [
            [1700000000000 + i * 3600000, 100 + i, 105 + i, 95 + i, 102 + i, 50.0]
            for i in range(50)
        ]
        df = validate_ohlcv(valid_rows, minimum=35)
        self.assertEqual(len(df), 50)
        self.assertTrue(df.index.is_monotonic_increasing)

        # High lower than close should fail
        bad_high = list(valid_rows)
        bad_high[5] = [bad_high[5][0], 100.0, 90.0, 80.0, 95.0, 10.0]
        with self.assertRaises(MarketDataError):
            validate_ohlcv(bad_high, minimum=35)

        # Duplicate timestamps should fail
        dup_rows = list(valid_rows)
        dup_rows[1][0] = dup_rows[0][0]
        with self.assertRaises(MarketDataError):
            validate_ohlcv(dup_rows, minimum=35)

    def test_daily_returns_and_pnl_formula(self):
        """Verify exact daily return formula: ((EOD / Prev_EOD) - 1) * 100 and Daily P&L."""
        dates = pd.date_range("2026-01-01", periods=3, freq="1D", tz="UTC")
        eq_data = [
            {"time": dates[0] + pd.Timedelta(hours=23), "equity": 10_000.0, "open_positions": 0},
            {"time": dates[1] + pd.Timedelta(hours=23), "equity": 10_200.0, "open_positions": 0},
            {"time": dates[2] + pd.Timedelta(hours=23), "equity": 9_996.0, "open_positions": 0},
        ]
        daily_df, monthly_df, metrics = compute_daily_returns_table(
            equity_curve=eq_data,
            trades=[],
            starting_capital=10_000.0,
            start_date=dates[0].date(),
            end_date=dates[2].date(),
        )
        self.assertEqual(len(daily_df), 3)

        # Day 1: starting 10000, ending 10000 -> 0%
        self.assertAlmostEqual(daily_df.loc["2026-01-01", "Daily Return %"], 0.0, places=4)
        self.assertAlmostEqual(daily_df.loc["2026-01-01", "Daily P&L"], 0.0, places=2)

        # Day 2: starting 10000, ending 10200 -> +2.0%, P&L +200
        self.assertAlmostEqual(daily_df.loc["2026-01-02", "Daily Return %"], 2.0, places=4)
        self.assertAlmostEqual(daily_df.loc["2026-01-02", "Daily P&L"], 200.0, places=2)

        # Day 3: starting 10200, ending 9996 -> -2.0%, P&L -204
        self.assertAlmostEqual(daily_df.loc["2026-01-03", "Daily Return %"], -2.0, places=4)
        self.assertAlmostEqual(daily_df.loc["2026-01-03", "Daily P&L"], -204.0, places=2)

    def test_mathematical_equity_reconciliation(self):
        """Verify that sum(Daily P&L) == Total Net P&L and compounded daily return == net return."""
        dates = pd.date_range("2026-01-01", periods=10, freq="1D", tz="UTC")
        eq_curve = []
        cur_eq = 10_000.0
        rng = np.random.RandomState(123)
        for d in dates:
            change = rng.normal(10, 50)
            cur_eq += change
            eq_curve.append({"time": d + pd.Timedelta(hours=23), "equity": cur_eq, "open_positions": 0})

        daily_df, _, metrics = compute_daily_returns_table(
            equity_curve=eq_curve,
            trades=[],
            starting_capital=10_000.0,
            start_date=dates[0].date(),
            end_date=dates[-1].date(),
        )

        sum_daily_pnl = float(daily_df["Daily P&L"].sum())
        total_pnl = metrics["Total Net P&L"]
        self.assertAlmostEqual(sum_daily_pnl, total_pnl, delta=0.05)

        # Compounding check
        compounded = 1.0
        for ret in daily_df["Daily Return %"]:
            compounded *= (1.0 + ret / 100.0)
        net_ret_from_compounded = (compounded - 1.0) * 100.0
        self.assertAlmostEqual(net_ret_from_compounded, metrics["Net Return %"], delta=0.05)

    def test_conservative_same_candle_sl_tp_priority(self):
        """When both stop-loss and take-profit are touched in the same candle, stop-loss wins."""
        tester = InstitutionalBacktester(fee_rate=0.0, slippage_rate=0.0)
        pos = {
            "side": "LONG",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "take_profit": 110.0,
        }
        # Bar touches low 90 (below 95) and high 115 (above 110)
        exit_result = tester._check_bar_exit(pos, bar_open=100.0, bar_high=115.0, bar_low=90.0)
        self.assertIsNotNone(exit_result)
        price, reason = exit_result
        self.assertIn("Stop loss", reason)
        self.assertIn("Conservative same-bar fill", reason)
        self.assertEqual(price, 95.0)

    def test_fees_and_slippage_accounting(self):
        """Verify fees and slippage are deducted correctly on entry and exit."""
        fee_rate = 0.001       # 0.1%
        slippage_rate = 0.002  # 0.2%
        tester = InstitutionalBacktester(fee_rate=fee_rate, slippage_rate=slippage_rate)

        # For LONG: entry is slipped up
        raw_entry = 100.0
        pos = tester._create_position(
            prefix_candles=[[1, 100, 105, 95, 100, 10]] * 50,
            raw_entry=raw_entry,
            side="LONG",
            timestamp=pd.Timestamp("2026-01-01", tz="UTC"),
            balance=10_000.0,
            open_positions=[],
            strategy_name="TEST",
            reason="test entry",
        )
        self.assertIsNotNone(pos)
        self.assertAlmostEqual(pos["entry_price"], 100.2, places=3)  # +0.2% slippage
        self.assertAlmostEqual(pos["entry_fee"], pos["entry_price"] * pos["quantity"] * fee_rate, places=4)

        # Close position
        trades = []
        tester._close_position(
            position=pos,
            closed_at=pd.Timestamp("2026-01-01 01:00", tz="UTC"),
            raw_exit=110.0,
            reason="Take profit",
            trades=trades,
        )
        self.assertEqual(len(trades), 1)
        t = trades[0]
        # Exit slipped down for LONG
        self.assertAlmostEqual(t["exit_price"], 110.0 * (1 - slippage_rate), places=3)
        self.assertGreater(t["fees"], 0.0)
        self.assertGreater(t["slippage_cost"], 0.0)
        # Net PnL must equal gross PnL minus fees
        self.assertAlmostEqual(t["net_pnl"], t["gross_pnl"] - t["fees"], delta=0.01)

    def test_position_sizing_and_risk_limit(self):
        """Position size must honor 1% risk budget and notional cannot exceed account balance (1x leverage)."""
        tester = InstitutionalBacktester(risk_fraction=0.01, starting_capital=10_000.0)
        pos = tester._create_position(
            prefix_candles=[[1, 100, 105, 95, 100, 10]] * 50,
            raw_entry=100.0,
            side="LONG",
            timestamp=pd.Timestamp("2026-01-01", tz="UTC"),
            balance=10_000.0,
            open_positions=[],
            strategy_name="TEST",
            reason="test risk",
        )
        self.assertIsNotNone(pos)
        # Notional <= account capital (1x leverage cap)
        self.assertLessEqual(pos["notional"], 10_000.0 * 1.000001)
        # Risk amount <= 1% of balance ($100)
        self.assertLessEqual(pos["risk_amount"], 100.0 * 1.000001)

    def test_lookahead_bias_prevention(self):
        """Signals up to candle T must be completely identical regardless of future candles."""
        base_frame = _synthetic_ohlcv(periods=150, seed=42)
        tester = InstitutionalBacktester()

        # Run on first 120 candles
        prefix_frame = base_frame.iloc[:120].copy()
        res_prefix = tester.run(prefix_frame, strategy_name="ARJUNA")

        # Now run on 150 candles where the last 30 candles are arbitrarily altered
        extended_frame = base_frame.copy()
        extended_frame.iloc[120:, extended_frame.columns.get_loc("close")] *= 2.5
        extended_frame.iloc[120:, extended_frame.columns.get_loc("high")] *= 3.0
        res_extended = tester.run(extended_frame, strategy_name="ARJUNA")

        # Trades closed before bar 120 must be byte-for-byte identical in both runs
        cutoff = prefix_frame.index[-1]
        trades_prefix = res_prefix["trades"].loc[res_prefix["trades"]["closed_at"] < cutoff]
        trades_extended = res_extended["trades"].loc[res_extended["trades"]["closed_at"] < cutoff]

        self.assertEqual(len(trades_prefix), len(trades_extended))
        if len(trades_prefix) > 0:
            pd.testing.assert_frame_equal(trades_prefix.reset_index(drop=True), trades_extended.reset_index(drop=True))

    def test_sharpe_and_sortino_edge_cases(self):
        """Zero variance or no losing days must not raise DivisionByZero errors."""
        dates = pd.date_range("2026-01-01", periods=5, freq="1D", tz="UTC")
        flat_eq = [{"time": d, "equity": 10_000.0, "open_positions": 0} for d in dates]
        _, _, metrics = compute_daily_returns_table(flat_eq, [], starting_capital=10_000.0)
        self.assertIsNone(metrics["Sharpe Ratio"])
        self.assertIsNone(metrics["Sortino Ratio"])

        # All positive returns (zero downside variance)
        winning_eq = [{"time": d, "equity": 10_000.0 + i * 100, "open_positions": 0} for i, d in enumerate(dates)]
        _, _, win_metrics = compute_daily_returns_table(winning_eq, [], starting_capital=10_000.0)
        self.assertIsNotNone(win_metrics["Sharpe Ratio"])
        self.assertIsNone(win_metrics["Sortino Ratio"])  # no downside trades

    def test_insufficient_and_empty_dataset(self):
        """Reject empty or undersized historical frames cleanly with MarketDataError."""
        tester = InstitutionalBacktester()
        with self.assertRaises(MarketDataError):
            tester.run(pd.DataFrame())

        small_frame = _synthetic_ohlcv(periods=50)
        with self.assertRaises(MarketDataError):
            tester.run(small_frame)

    def test_out_of_sample_split_integrity(self):
        """Verify out-of-sample chronological partition without data leakage."""
        frame = _synthetic_ohlcv(periods=200, seed=99)
        tester = InstitutionalBacktester()
        result = tester.run(frame, strategy_name="ARJUNA", out_of_sample_ratio=0.3)
        oos = result["out_of_sample"]
        self.assertIsNotNone(oos)
        self.assertEqual(oos["split_ratio"], 0.3)
        self.assertIn("in_sample", oos)
        self.assertIn("out_of_sample", oos)
        self.assertGreater(oos["in_sample"]["Days"], 0)
        self.assertGreater(oos["out_of_sample"]["Days"], 0)

    def test_backtest_reproducibility(self):
        """Two consecutive runs on the same dataset must produce identical outputs."""
        frame = _synthetic_ohlcv(periods=180, seed=777)
        tester = InstitutionalBacktester()
        run1 = tester.run(frame, strategy_name="ARJUNA")
        run2 = tester.run(frame, strategy_name="ARJUNA")

        self.assertEqual(run1["metrics"]["Total Net P&L"], run2["metrics"]["Total Net P&L"])
        self.assertEqual(run1["metrics"]["Total Trades"], run2["metrics"]["Total Trades"])
        pd.testing.assert_frame_equal(run1["trades"], run2["trades"])
        pd.testing.assert_frame_equal(run1["daily_table"], run2["daily_table"])


if __name__ == "__main__":
    unittest.main()
