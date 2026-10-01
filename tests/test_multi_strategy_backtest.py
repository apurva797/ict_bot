import io
import unittest
from unittest.mock import patch
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
from streamlit.testing.v1 import AppTest

from demo_data import MarketDataError, fetch_historical_market_data, load_uploaded_ohlcv
from multi_strategy_backtest import WARMUP_CANDLES, run_multi_strategy_backtest

ROOT = Path(__file__).resolve().parents[1]


def candles(count=106):
    index = pd.date_range("2026-01-01", periods=count, freq="h", tz="UTC")
    close = pd.Series(100.0, index=index)
    return pd.DataFrame({"open": close, "high": close + 0.5, "low": close - 0.5,
                         "close": close, "volume": 100.0}, index=index)


def make_analyzer(side="LONG", confirmed=True, on_length=100, heavy=None):
    seen = []

    def analyzer(rows, strategy_selection=None, historical=False):
        seen.append((rows[-1][0], len(rows), historical, tuple(strategy_selection or ())))
        directional = side if len(rows) == on_length else "NEUTRAL"
        return {
            "signals": {"TREND": {"side": directional, "score": 80, "reason": "test signal"}},
            "final_signal": {
                "side": directional, "confirmation_passed": confirmed,
                "confirmation_points": 4 if confirmed else 2,
                "heavy_conditions": heavy or [], "active_long": ["TREND"] if directional == "LONG" else [],
                "active_short": ["TREND"] if directional == "SHORT" else [],
                "reason": "test confirmation",
            },
        }
    analyzer.seen = seen
    return analyzer


class HistoricalBacktestTests(unittest.TestCase):
    def run_bt(self, frame=None, **kwargs):
        analyzer = kwargs.pop("analyzer", make_analyzer())
        result = run_multi_strategy_backtest(frame if frame is not None else candles(),
                                             analyzer=analyzer, **kwargs)
        return result, analyzer

    def test_historical_iteration_uses_prefix_through_signal_candle_only(self):
        result, analyzer = self.run_bt()
        self.assertEqual(len(analyzer.seen), len(candles()) - WARMUP_CANDLES)
        first_signal_timestamp, first_prefix_length, historical, _ = analyzer.seen[0]
        self.assertEqual(first_prefix_length, WARMUP_CANDLES)
        self.assertTrue(historical)
        # First simulated fill is the candle immediately after the first prefix.
        self.assertLess(pd.to_datetime(first_signal_timestamp, unit="ms", utc=True), candles().index[WARMUP_CANDLES])
        self.assertIsNotNone(result["equity"].index.name)

    def test_one_hundred_candle_warmup_and_empty_invalid_data(self):
        with self.assertRaises(MarketDataError):
            run_multi_strategy_backtest(candles(101), analyzer=make_analyzer())
        with self.assertRaises(MarketDataError):
            run_multi_strategy_backtest(pd.DataFrame(), analyzer=make_analyzer())
        invalid = candles()
        invalid.iloc[5, invalid.columns.get_loc("low")] = 500
        with self.assertRaises(MarketDataError):
            run_multi_strategy_backtest(invalid, analyzer=make_analyzer())

    def test_long_entry_next_open_take_profit_fees_slippage_and_pnl(self):
        frame = candles()
        frame.iloc[101, frame.columns.get_loc("high")] = 106
        result, _ = self.run_bt(frame, analyzer=make_analyzer("LONG"))
        trade = result["trades"].iloc[0]
        self.assertEqual(trade.side, "LONG")
        self.assertEqual(trade.exit_reason, "Take profit")
        self.assertEqual(trade.opened_at, frame.index[100])
        self.assertGreater(trade.fees, 0)
        self.assertGreater(trade.slippage_cost, 0)
        self.assertAlmostEqual(trade.pnl, result["metrics"]["Final capital"] - 10_000.0)
        self.assertEqual(result["metrics"]["Long trades"], 1)

    def test_short_trade_stop_loss_and_same_candle_stop_priority(self):
        frame = candles()
        frame.iloc[101, frame.columns.get_loc("high")] = 106
        frame.iloc[101, frame.columns.get_loc("low")] = 94
        result, _ = self.run_bt(frame, analyzer=make_analyzer("SHORT"))
        trade = result["trades"].iloc[0]
        self.assertEqual(trade.side, "SHORT")
        self.assertEqual(trade.exit_reason, "Stop loss")
        self.assertEqual(result["metrics"]["Short trades"], 1)

    def test_target_take_profit_and_drawdown_metrics_are_available(self):
        frame = candles()
        frame.iloc[101, frame.columns.get_loc("high")] = 106
        result, _ = self.run_bt(frame)
        self.assertEqual(result["trades"].iloc[0].exit_reason, "Take profit")
        self.assertIn("drawdown_pct", result["equity"])
        self.assertIn("Max drawdown %", result["metrics"])

    def test_risk_reward_minimum_and_default_target(self):
        accepted, _ = self.run_bt(target_rr=1.5)
        self.assertGreaterEqual(accepted["trades"].iloc[0].rr, 1.5)
        with self.assertRaises(ValueError):
            self.run_bt(target_rr=1.49)
        defaulted, _ = self.run_bt()
        self.assertAlmostEqual(defaulted["trades"].iloc[0].rr, 2.0)

    def test_confirmation_blocked_signal_and_heavy_confirmation(self):
        blocked, _ = self.run_bt(analyzer=make_analyzer("LONG", confirmed=False))
        self.assertEqual(blocked["metrics"]["Blocked signals"], len(blocked["blocked_signals"]))
        self.assertGreaterEqual(blocked["metrics"]["Blocked signals"], 1)
        self.assertTrue((blocked["blocked_signals"].status == "BLOCKED").all())
        heavy, _ = self.run_bt(analyzer=make_analyzer("LONG", confirmed=True, heavy=["TREND", "ICT"]))
        self.assertEqual(heavy["metrics"]["Executed signals"], 1)

    def test_multi_strategy_selection_and_all_strategy_breakdown(self):
        result, analyzer = self.run_bt(strategy_selection=["TREND", "ICT"])
        self.assertEqual(analyzer.seen[0][3], ("TREND", "ICT"))
        self.assertEqual(len(result["strategy_breakdown"]), 15)
        self.assertIn("ARJUNA", set(result["strategy_breakdown"].Strategy))
        self.assertIn("TREND", set(result["strategy_breakdown"].Strategy))

    def test_metrics_include_statistics_and_position_sizing_caps(self):
        result, _ = self.run_bt()
        metrics = result["metrics"]
        self.assertEqual(metrics["Total trades"], 1)
        self.assertIn("Average R", metrics)
        self.assertIn("Maximum consecutive wins", metrics)
        self.assertIn("Maximum consecutive losses", metrics)
        self.assertLessEqual(result["trades"].iloc[0].risk_amount, 100.0 + 1e-8)

    def test_upload_csv_loader_and_historical_range_adapter(self):
        frame = candles()
        csv = frame.rename_axis("timestamp").reset_index().to_csv(index=False).encode()
        uploaded = load_uploaded_ohlcv(io.BytesIO(csv))
        self.assertEqual(len(uploaded), len(frame))
        loaded = fetch_historical_market_data(
            "BTC/USDT", "1h", frame.index[0], frame.index[-1] + pd.Timedelta(hours=1), csv_data=io.BytesIO(csv)
        )
        self.assertEqual(loaded.source, "Uploaded OHLCV CSV")
        self.assertEqual(len(loaded.frame), len(frame))

    def test_public_historical_binance_provider_and_source_metadata(self):
        start = pd.Timestamp("2026-01-01", tz="UTC")
        rows = []
        for i in range(110):
            ts = int((start + pd.Timedelta(hours=i)).timestamp() * 1000)
            rows.append([ts, 100, 101, 99, 100, 25])
        exchange = Mock()
        exchange.fetch_ohlcv.return_value = rows
        with patch("demo_data.ccxt.binance", return_value=exchange):
            result = fetch_historical_market_data(
                "BTC/USDT", "1h", start, start + pd.Timedelta(hours=110)
            )
        self.assertEqual(result.source, "Binance public historical OHLCV")
        self.assertFalse(result.used_fallback)
        self.assertEqual(len(result.frame), 110)

    def test_historical_range_validation_and_no_live_crypto_request(self):
        with self.assertRaises(MarketDataError):
            fetch_historical_market_data("BTC/USDT", "1h", "2026-01-02", "2026-01-01", csv_data=b"")
        analyzer = make_analyzer()
        with patch("bot.get_crypto_signal", side_effect=AssertionError("live data called")):
            result, _ = self.run_bt(analyzer=analyzer)
        self.assertEqual(result["metrics"]["Total trades"], 1)

    def test_streamlit_has_separate_backtesting_section(self):
        app = AppTest.from_file(str(ROOT / "app.py")).run()
        self.assertFalse(list(app.exception))
        self.assertTrue(any(item.label == "Run historical backtest" for item in app.button))


if __name__ == "__main__":
    unittest.main()
