from core.compliance.output_guard import guard_text


def test_guard_removes_direct_recommendation():
    safe, changed = guard_text("BUY RELIANCE after results.")
    assert changed is True
    assert "BUY RELIANCE" not in safe
    assert "direct recommendation removed" in safe


def test_guard_preserves_neutral_text():
    safe, changed = guard_text("Historical revenue increased in FY2025.")
    assert changed is False
    assert safe == "Historical revenue increased in FY2025."
