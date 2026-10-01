"""Shared pytest fixtures.

Streamlit's ``st.cache_data`` is process-global, so a dataset cached by one
AppTest would otherwise be served to the next test that mocks a different
provider. Every test starts from a clean cache and session.
"""

import pytest
import streamlit as st


@pytest.fixture(autouse=True)
def isolated_streamlit_state():
    """Reset the global Streamlit cache and session between tests."""
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

    ``app.py`` imports ``fetch_live_market_snapshot`` inside the fragment, so
    it resolves the module attribute at call time and picks up this patch.
    Tests that genuinely exercise the snapshot mark themselves with
    ``@pytest.mark.exercise_live_snapshot``; the rest either patch their own
    value or never touch it.
    """
    import demo_data

    if request.node.get_closest_marker("exercise_live_snapshot"):
        yield
        return
    from unittest.mock import patch

    with patch.object(demo_data, "fetch_live_market_snapshot",
                      side_effect=demo_data.MarketDataError(
                          "Live monitoring is disabled during tests.")):
        yield