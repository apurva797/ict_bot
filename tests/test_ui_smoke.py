"""End-to-end smoke coverage for the routed shell and the strategy panels.

The screens are wired through session state, so a broken panel surfaces as a
Streamlit exception rather than as a failing unit. These tests walk the app the
way a user does — every route, every strategy panel, and an explicit chart load
for each — and assert that nothing raises and that the results actually render.

Market data is always stubbed. The autouse ``no_implicit_live_market_calls``
fixture blocks ticker snapshots, and the provider is replaced here so the tests
never depend on the network.
"""

import unittest
from unittest.mock import patch

import demo_data
from conftest import click, go_to, open_app, pick_strategy

ROUTES = ("Home", "Markets", "Trade", "Portfolio", "Research")
STRATEGIES = ("ARJUNA Strategy", "Quant Strategy", "AI Strategy",
              "Multi-Strategy Engine")

# The label of the paper control on the multi-strategy panel, which must stay
# on screen after an analysis run published its results.
MULTI_PAPER_CONTROL = "Update multi-strategy paper account"


def sample_result(limit: int = 500) -> demo_data.MarketDataResult:
    """A validated frame from the bundled sample, labelled as backup data."""
    return demo_data.MarketDataResult(
        demo_data._load_sample_ohlcv("BTC/USDT", "1h", limit),
        "Bundled historical sample (not live)", True,
    )


class ScreenSmokeTests(unittest.TestCase):
    """Every route and every panel must render without a Streamlit exception."""

    def test_every_route_renders_without_exceptions(self):
        with patch("demo_data.fetch_market_data", return_value=sample_result()):
            for route in ROUTES:
                with self.subTest(route=route):
                    app = go_to(open_app(), route)
                    self.assertFalse(list(app.exception))

    def test_every_strategy_panel_renders_without_exceptions(self):
        with patch("demo_data.fetch_market_data", return_value=sample_result()):
            for strategy in STRATEGIES:
                with self.subTest(strategy=strategy):
                    app = pick_strategy(open_app(), strategy)
                    self.assertFalse(list(app.exception))

    def test_loading_the_chart_publishes_candles_for_every_strategy(self):
        """The chart slot is filled in the same run as the load, not a run later."""
        with patch("demo_data.fetch_market_data", return_value=sample_result()):
            for strategy in STRATEGIES:
                with self.subTest(strategy=strategy):
                    app = pick_strategy(open_app(), strategy)
                    app = click(app, "Load chart")
                    self.assertFalse(list(app.exception))
                    snapshot = app.session_state["chart_snapshot"]
                    self.assertEqual(snapshot["symbol"], "BTC/USDT")
                    self.assertGreater(len(snapshot["frame"]), 0)

    def test_multi_strategy_analysis_keeps_its_paper_controls_on_screen(self):
        """Regression: the controls used to vanish on the run after analysis."""
        with patch("demo_data.fetch_market_data", return_value=sample_result(1000)):
            app = pick_strategy(open_app(), "Multi-Strategy Engine")
            app = click(app, "Analyze all strategies")
            self.assertFalse(list(app.exception))
            self.assertTrue(any(item.label == MULTI_PAPER_CONTROL
                                for item in app.button))
            # A plain rerun must not drop the controls either.
            app.run()
            self.assertFalse(list(app.exception))
            self.assertTrue(any(item.label == MULTI_PAPER_CONTROL
                                for item in app.button))

    def test_a_failed_provider_load_states_the_problem_instead_of_blanking(self):
        """On failure the screen must explain itself, not render stale candles."""
        with patch("demo_data.fetch_market_data",
                   side_effect=demo_data.MarketDataError("provider chain blocked")):
            app = pick_strategy(open_app(), "ARJUNA Strategy")
            app = click(app, "Check latest ARJUNA signal")
            self.assertFalse(list(app.exception))
            self.assertNotIn("chart_snapshot", app.session_state)
            self.assertTrue(any("provider chain blocked" in item.value
                                for item in app.markdown))


if __name__ == "__main__":
    unittest.main()
