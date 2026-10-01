import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pandas as pd

from demo_data import MarketDataError, fetch_live_market_snapshot
from demo_paper import monitor_paper_position
from portfolio import portfolio_snapshot


def position(side="SHORT"):
    entry = 100.0
    stop = 101.0 if side == "SHORT" else 99.0
    target = 98.0 if side == "SHORT" else 102.0
    return {"side": side, "entry": entry, "stop": stop, "target": target,
            "quantity": 1.0, "risk_distance": 1.0, "risk_amount": 10.0,
            "opened_at": "2026-01-01T00:00:00+00:00", "journal_context": {"strategy_id": "multi.strategy"}}


def snapshot(price, high=None, low=None):
    now = pd.Timestamp("2026-01-01T01:00:00Z")
    return {"price": price, "updated_at": now, "source": "Test Coinbase market data",
            "candle": {"timestamp": now.floor("5min"), "open": 100.0,
                       "high": high if high is not None else max(100.0, price),
                       "low": low if low is not None else min(100.0, price), "close": price, "volume": 10}}


class MultiStrategyPaperMonitorTests(unittest.TestCase):
    def state(self, side="SHORT"):
        return {"strategy_id": "multi.strategy", "market": "BTC/USDT", "timeframe": "5m",
                "starting_capital": 10_000.0, "balance": 10_000.0,
                "position": position(side), "trades": []}

    def test_short_target_and_stop_close_exactly_once(self):
        for price, expected in ((97.0, "TP"), (102.0, "SL")):
            with self.subTest(expected=expected):
                state = self.state("SHORT")
                result = monitor_paper_position(state, snapshot(price))
                self.assertTrue(result["closed"])
                self.assertEqual(result["trade"]["exit_reason"], expected)
                self.assertIsNone(state["position"])
                before = state["balance"]
                again = monitor_paper_position(state, snapshot(price))
                self.assertFalse(again["closed"])
                self.assertEqual(len(state["trades"]), 1)
                self.assertEqual(state["balance"], before)

    def test_long_target_and_stop_close(self):
        for price, expected in ((103.0, "TP"), (98.0, "SL")):
            with self.subTest(expected=expected):
                state = self.state("LONG")
                result = monitor_paper_position(state, snapshot(price))
                self.assertEqual(result["trade"]["exit_reason"], expected)

    def test_candle_extremes_trigger_and_same_candle_stop_wins(self):
        state = self.state("SHORT")
        result = monitor_paper_position(state, snapshot(100, high=102, low=97))
        self.assertEqual(result["trade"]["exit_reason"], "SL")
        long_state = self.state("LONG")
        result = monitor_paper_position(long_state, snapshot(100, high=103, low=98))
        self.assertEqual(result["trade"]["exit_reason"], "SL")

    def test_open_pnl_r_multiple_duration_journal_and_portfolio(self):
        state = self.state("SHORT")
        open_result = monitor_paper_position(state, snapshot(99))
        self.assertEqual(open_result["status"], "OPEN — TARGET NOT HIT")
        portfolio = portfolio_snapshot([state])
        self.assertEqual(portfolio["positions"], 1)
        self.assertGreater(portfolio["unrealized_pnl"], 0)
        close_result = monitor_paper_position(state, snapshot(97))
        trade = close_result["trade"]
        self.assertEqual(state["balance"], 10_000 + trade["net_pnl"])
        self.assertAlmostEqual(trade["r_multiple"], trade["net_pnl"] / trade["risk_amount"])
        self.assertEqual(trade["duration_minutes"], 60)
        self.assertEqual(len(state["trades"]), 1)
        self.assertEqual(portfolio_snapshot([state])["positions"], 0)

    def test_live_snapshot_reads_coinbase_and_rejects_stale_or_failed_data(self):
        now = pd.Timestamp.now(tz="UTC")
        bucket = now.floor("5min")
        ticker = Mock()
        ticker.json.return_value = {"price": "100", "time": now.isoformat()}
        candle = Mock()
        candle.json.return_value = [[int(bucket.timestamp()), 98, 101, 99, 100, 4]]
        session = Mock()
        session.get.side_effect = [ticker, candle]
        result = fetch_live_market_snapshot(session=session)
        self.assertEqual(result["price"], 100)
        self.assertEqual(result["source"], "Coinbase Exchange public market data")

        stale = Mock()
        stale.json.return_value = {"price": "100", "time": "2020-01-01T00:00:00Z"}
        session.get.side_effect = [stale]
        with self.assertRaises(MarketDataError):
            fetch_live_market_snapshot(session=session)
        failed = Mock()
        failed.get.side_effect = OSError("network down")
        with self.assertRaises(MarketDataError) as error:
            fetch_live_market_snapshot(session=failed)
        self.assertIn("network down", str(error.exception))


if __name__ == "__main__":
    unittest.main()
