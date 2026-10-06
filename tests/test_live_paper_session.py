import unittest
from unittest.mock import patch

import demo_data
from conftest import click, go_to, open_app, rendered


class LivePaperSessionTests(unittest.TestCase):
    def test_start_and_stop_use_fresh_non_sample_data(self):
        frame = demo_data._load_sample_ohlcv("BTC/USDT", "1h", 500)
        live_result = demo_data.MarketDataResult(frame, "Test live provider", False)
        with patch("demo_data.fetch_market_data", return_value=live_result):
            app = go_to(open_app(), "Trade")
            app = click(app, "Start paper session")
            self.assertFalse(list(app.exception))
            self.assertTrue(app.session_state["live_paper_session"]["active"])
            self.assertEqual(app.session_state["live_paper_session"]["last_source"],
                             "Test live provider")
            self.assertIn("PAPER ENGINE RUNNING", rendered(app))
            app = click(app, "Stop paper session")
            self.assertFalse(app.session_state["live_paper_session"]["active"])

    def test_sample_data_cannot_claim_a_running_live_session(self):
        frame = demo_data._load_sample_ohlcv("BTC/USDT", "1h", 500)
        sample_result = demo_data.MarketDataResult(
            frame, "Bundled historical sample (not live)", True)
        with patch("demo_data.fetch_market_data", return_value=sample_result):
            app = go_to(open_app(), "Trade")
            app = click(app, "Start paper session")
            self.assertFalse(app.session_state["live_paper_session"]["active"])
            self.assertIn("Live paper session stopped", app.session_state["live_paper_session"]["last_error"])
