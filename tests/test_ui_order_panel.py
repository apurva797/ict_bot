"""Order panel must never bypass the existing risk engine."""

import pandas as pd

import ui.orderpanel as op
from platform_core.status import DataHealth

NOW = pd.Timestamp("2026-01-01", tz="UTC")
ACCOUNT = {"balance": 10_000.0}


def intent(side="LONG", entry=100.0, stop=99.0, target=102.0):
    return op.OrderIntent("BTC/USDT", "5m", side, entry, stop, target,
                          "ict", "Arjun", "test", NOW)


def test_engine_sizes_and_reports_the_real_decision():
    decision = op.evaluate(intent(), ACCOUNT)
    assert decision.approved
    assert decision.quantity > 0
    assert decision.risk_amount == 100.0          # 1% of a 10k account
    assert round(decision.actual_rr, 4) == 2.0
    assert round(decision.notional, 2) == 10_000.0  # 1x leverage cap


def test_preview_states_the_loss_if_the_stop_is_hit():
    markup = op.preview(op.evaluate(intent(), ACCOUNT), 10_000.0)
    assert "estimated loss = $100.00" in markup
    assert "estimated profit = $200.00" in markup


def test_every_risk_guarded_path_still_blocks():
    cases = {
        "sub-1.5R target": op.evaluate(intent(target=100.5), ACCOUNT),
        "inverted levels": op.evaluate(intent(stop=101.0), ACCOUNT),
        "max open positions": op.evaluate(intent(), ACCOUNT, open_positions=1),
        "stale data": op.evaluate(intent(), ACCOUNT, data_health=DataHealth.STALE),
        "daily trade cap": op.evaluate(intent(), ACCOUNT, daily_trades=3),
        "daily loss cap": op.evaluate(intent(), ACCOUNT, daily_r=-2.0),
        "cooldown": op.evaluate(intent(), ACCOUNT, cooldown_active=True),
    }
    for label, decision in cases.items():
        assert not decision.approved, label
        assert decision.reason, label


def test_geometry_check_rejects_impossible_levels():
    assert op.levels_from_signal("LONG", 100.0, 99.0, 102.0) == (100.0, 99.0)
    assert op.levels_from_signal("SHORT", 100.0, 101.0, 98.0) == (100.0, 101.0)
    assert op.levels_from_signal("LONG", 100.0, 101.0, 102.0) == (0.0, 0.0)
    assert op.levels_from_signal("NEUTRAL", 100.0, 99.0, 102.0) == (0.0, 0.0)


def test_blocked_preview_reports_the_engine_reason():
    markup = op.blocked_preview(op.evaluate(intent(target=100.5), ACCOUNT))
    assert "Risk blocked" in markup
    assert "No position was sized" in markup