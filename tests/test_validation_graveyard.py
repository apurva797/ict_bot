import json
import sqlite3

import pytest

from engine.validation_graveyard import (
    get_run_events,
    get_strategy_history,
    initialize_graveyard,
    list_graveyard_entries,
    record_validation_report,
)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "graveyard.sqlite3"


def report(strategy_id="alpha", status="Rejected", **extra):
    value = {
        "strategy_id": strategy_id,
        "status": status,
        "checks": [{"name": "oos", "status": "FAIL", "detail": "OOS failed"}],
        "reasons": ["OOS failed"],
        "evidence_reference": "backtest-001",
    }
    value.update(extra)
    return value


def test_initializes_database_and_tables(db_path):
    initialize_graveyard(db_path)
    with sqlite3.connect(db_path) as conn:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert {"validation_runs", "graveyard_events"} <= names


def test_rejected_run_is_listed_with_reason_and_evidence(db_path):
    run_id = record_validation_report(report(), "v2", db_path=db_path)
    entries = list_graveyard_entries(db_path)
    assert len(entries) == 1
    assert entries[0]["run_id"] == run_id
    assert entries[0]["strategy_version"] == "v2"
    assert entries[0]["report"]["reasons"] == ["OOS failed"]
    assert entries[0]["evidence_reference"] == "backtest-001"


def test_incomplete_run_is_listed_by_default(db_path):
    record_validation_report(report(status="Incomplete"), db_path=db_path)
    assert list_graveyard_entries(db_path)[0]["status"] == "Incomplete"


def test_eligible_run_not_listed_as_graveyard(db_path):
    record_validation_report(report(status="Eligible"), db_path=db_path)
    assert list_graveyard_entries(db_path) == []


def test_re_evaluation_preserves_old_rejection(db_path):
    record_validation_report(report(status="Rejected"), "v1", db_path=db_path)
    record_validation_report(report(status="Eligible"), "v2", db_path=db_path)
    history = get_strategy_history("alpha", db_path)
    assert [item["status"] for item in history] == ["Rejected", "Eligible"]
    assert len(list_graveyard_entries(db_path)) == 1


def test_history_can_filter_version(db_path):
    record_validation_report(report(), "v1", db_path=db_path)
    record_validation_report(report(), "v2", db_path=db_path)
    assert len(get_strategy_history("alpha", db_path, "v1")) == 1


def test_event_created_for_each_run(db_path):
    run_id = record_validation_report(report(), db_path=db_path, note="review needed")
    events = get_run_events(run_id, db_path)
    assert len(events) == 1
    assert events[0]["event_type"] == "rejected"
    assert events[0]["note"] == "review needed"


@pytest.mark.parametrize("bad_report", [
    {"status": "Rejected"},
    {"strategy_id": "alpha", "status": "Unknown"},
])
def test_invalid_report_rejected(db_path, bad_report):
    with pytest.raises(ValueError):
        record_validation_report(bad_report, db_path=db_path)


def test_blank_version_rejected(db_path):
    with pytest.raises(ValueError):
        record_validation_report(report(), " ", db_path=db_path)


def test_limit_must_be_positive(db_path):
    with pytest.raises(ValueError):
        list_graveyard_entries(db_path, limit=0)
