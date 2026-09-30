"""Fail-closed safety controls for the public, simulation-only demo."""

import os

DEMO_MODE = True  # Deliberately constant: no UI or environment override exists.
LIVE_ORDERS_ENABLED = False
MAX_RISK_FRACTION = 0.01
MIN_RISK_REWARD = 2.0
MAX_LEVERAGE = 1.0
COOLDOWN_MINUTES = 30


class SafetyError(ValueError):
    """A requested simulation violates a fixed demo safety limit."""


def assert_demo_mode() -> None:
    if not DEMO_MODE or LIVE_ORDERS_ENABLED:
        raise SafetyError("Execution is blocked: demo mode safety check failed.")


def validate_risk_controls(risk_fraction=0.01, rr=2.0, leverage=1.0):
    assert_demo_mode()
    if risk_fraction > MAX_RISK_FRACTION or risk_fraction <= 0:
        raise SafetyError("Strategy rejected because risk exceeds the 1% demo limit.")
    if rr < MIN_RISK_REWARD:
        raise SafetyError("Strategy rejected because minimum risk/reward is 2.0.")
    if leverage > MAX_LEVERAGE or leverage <= 0:
        raise SafetyError("Strategy rejected because leverage exceeds the 1x demo limit.")


def reject_live_order(*_args, **_kwargs):
    """Compatibility guard for any accidental route into a broker order API."""
    assert_demo_mode()
    raise SafetyError("Live orders are disabled in this application.")


def ict_entry_gate(timestamp, news_blackout=False, last_trade_at=None):
    """Validate ICT news/cooldown gates; ICT entries are eligible 24/7."""
    assert_demo_mode()
    if news_blackout:
        return False, "Configured news blackout is active."
    if last_trade_at is not None and (timestamp - last_trade_at).total_seconds() < COOLDOWN_MINUTES * 60:
        return False, "30 minute cooldown is active."
    return True, "ICT entry gates passed."


def has_optional_llm_key():
    return bool(os.getenv("OPENAI_API_KEY", "").strip())
