"""Shared helpers and fixtures for the Streamlit end-to-end tests.

The app is a routed shell, so a test that exercises the Trade screen must first
navigate there. These helpers keep that navigation in one place instead of
repeating the widget-index dance in every test, and they fail loudly with the
available routes when navigation is not found.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1] / "app.py"


@pytest.fixture(autouse=True)
def isolated_streamlit_state():
    """Reset the global Streamlit cache and session between tests.

    Streamlit's ``st.cache_data`` is process-global, so a dataset cached by one
    AppTest would otherwise be served to the next test that mocks a different
    provider. Every test starts from a clean cache and session.
    """
    import demo_data

    demo_data._SNAPSHOT_CACHE.clear()
    st.cache_data.clear()
    st.cache_resource.clear()
    yield
    demo_data._SNAPSHOT_CACHE.clear()
    st.cache_data.clear()
    st.cache_resource.clear()


@pytest.fixture(autouse=True)
def no_implicit_live_market_calls(request):
    """Stop AppTest runs from making real network calls by accident.

    Market data is imported inside the screens, so it resolves the module
    attribute at call time and picks up this patch. Tests that genuinely
    exercise the snapshot mark themselves with
    ``@pytest.mark.exercise_live_snapshot``; the rest either patch their own
    value or never touch it.
    """
    import demo_data

    if request.node.get_closest_marker("exercise_live_snapshot"):
        yield
        return
    with patch.object(demo_data, "fetch_live_market_snapshot",
                      side_effect=demo_data.MarketDataError(
                          "Live monitoring is disabled during tests.")):
        yield


# ---------------------------------------------------------------------------
# Navigation helpers
#
# The app is a routed shell, so a test that exercises the Trade screen must
# first navigate there. Keeping that navigation here means each test reads as
# what it is testing, and a missing control fails with the available options
# instead of an obscure AttributeError.
# ---------------------------------------------------------------------------


def open_app() -> AppTest:
    """Load the app at its default route."""
    return AppTest.from_file(str(ROOT)).run(timeout=30)


def go_to(app: AppTest, route: str) -> AppTest:
    """Navigate to a screen and return the app after the rerun."""
    control = next((item for item in app.segmented_control
                    if route in (item.options or ())), None)
    if control is None:
        available = [list(getattr(item, "options", ()) or ())
                     for item in app.segmented_control]
        raise AssertionError(f"No navigation offers {route!r}. Found: {available}")
    return control.set_value(route).run(timeout=30)


def open_trade(app: AppTest | None = None) -> AppTest:
    """Navigate to the Trade screen, where the strategy selector lives."""
    return go_to(app if app is not None else open_app(), "Trade")


def strategy_radio(app: AppTest):
    """The strategy radio, which only exists on the Trade screen."""
    radio = next((item for item in app.radio if item.label == "Strategy"), None)
    if radio is None:
        routes = [list(getattr(item, "options", ()) or ())
                  for item in app.segmented_control]
        raise AssertionError(
            "The Strategy selector is not on screen. Navigate to Trade first. "
            f"Available routes: {routes}")
    return radio


def pick_strategy(app: AppTest, label: str) -> AppTest:
    """Select a strategy, navigating to the Trade screen when needed."""
    if not any(item.label == "Strategy" for item in app.radio):
        app = go_to(app, "Trade")
    return strategy_radio(app).set_value(label).run(timeout=30)


def click(app: AppTest, label: str) -> AppTest:
    """Click a button by its exact label and rerun."""
    button = next((item for item in app.button if item.label == label), None)
    if button is None:
        raise AssertionError(
            f"No button labelled {label!r}. Available: "
            f"{sorted(item.label for item in app.button)}")
    return button.click().run(timeout=30)


def rendered(app: AppTest) -> str:
    """Every text-ish element on the page, joined for substring assertions.

    The redesigned UI renders results as HTML cards via ``st.markdown``, so
    tests assert on the rendered text rather than on ``st.metric`` labels.
    """
    from itertools import chain

    parts: list[str] = []
    text_lists = (app.markdown, app.caption, app.title, app.header,
                  app.subheader, app.text, app.info, app.warning, app.error,
                  app.success, app.exception)
    for element in chain.from_iterable(text_lists):
        value = getattr(element, "value", None)
        if value:
            parts.append(str(value))
    widget_lists = (app.button, app.checkbox, app.selectbox, app.radio,
                    app.multiselect, app.slider, app.text_input,
                    app.number_input, app.text_area)
    for widget in chain.from_iterable(widget_lists):
        label = getattr(widget, "label", None)
        if label:
            parts.append(str(label))
    return "\n".join(parts)
