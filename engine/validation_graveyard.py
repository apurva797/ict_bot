"""Persistent, append-only validation history for Arjuna's Strategy Graveyard.

This module stores validation reports; it does not run backtests and does not
promote a strategy to paper/live trading. Eligible reports are retained in run
history but are not listed as graveyard entries.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_db_path() -> Path:
    configured = os.environ.get("ARJUNA_VALIDATION_DB")
    if configured:
        return Path(configured).expanduser()
    # Keep runtime data out of the source modules; allow tests to pass a temp DB.
    return Path("data") / "validation_graveyard.sqlite3"


def _connect(db_path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else _default_db_path()
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_graveyard(db_path: str | os.PathLike[str] | None = None) -> None:
    """Create the append-only run/event tables if they do not exist."""
    with _connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS validation_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('Eligible', 'Rejected', 'Incomplete')),
                evidence_reference TEXT,
                report_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_validation_runs_strategy
            ON validation_runs(strategy_id, strategy_version, run_id)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS graveyard_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES validation_runs(run_id),
                event_type TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_graveyard_events_run
            ON graveyard_events(run_id, event_id)
        """)


def record_validation_report(
    report: Mapping[str, Any],
    strategy_version: str = "v1",
    note: str = "",
    db_path: str | os.PathLike[str] | None = None,
) -> int:
    """Persist a report and an audit event; return its run ID.

    Every attempt is retained, including later Eligible re-evaluations. Rejected
    and Incomplete runs appear in the graveyard view; Eligible runs remain in
    history so the prior rejection is never erased.
    """
    strategy_id = str(report.get("strategy_id") or "").strip()
    status = str(report.get("status") or "").strip()
    if not strategy_id:
        raise ValueError("report.strategy_id is required")
    if status not in {"Eligible", "Rejected", "Incomplete"}:
        raise ValueError("report.status must be Eligible, Rejected, or Incomplete")
    version = str(strategy_version or "").strip()
    if not version:
        raise ValueError("strategy_version must not be empty")
    payload = dict(report)
    payload.setdefault("strategy_version", version)
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    evidence_reference = report.get("evidence_reference")
    initialize_graveyard(db_path)
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO validation_runs
               (strategy_id, strategy_version, status, evidence_reference, report_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (strategy_id, version, status,
             str(evidence_reference).strip() if evidence_reference else None,
             payload_json, _utc_now()),
        )
        run_id = int(cursor.lastrowid)
        event_type = "eligible_re_evaluation" if status == "Eligible" else status.lower()
        conn.execute(
            """INSERT INTO graveyard_events (run_id, event_type, note, created_at)
               VALUES (?, ?, ?, ?)""",
            (run_id, event_type, str(note or ""), _utc_now()),
        )
        return run_id


def list_graveyard_entries(
    db_path: str | os.PathLike[str] | None = None,
    include_incomplete: bool = True,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return latest Rejected/Incomplete runs; Eligible runs are excluded."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    initialize_graveyard(db_path)
    statuses = ("Rejected", "Incomplete") if include_incomplete else ("Rejected",)
    placeholders = ",".join("?" for _ in statuses)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""SELECT run_id, strategy_id, strategy_version, status,
                       evidence_reference, report_json, created_at
                FROM validation_runs
                WHERE status IN ({placeholders})
                ORDER BY run_id DESC LIMIT ?""",
            (*statuses, int(limit)),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["report"] = json.loads(item.pop("report_json"))
        result.append(item)
    return result


def get_strategy_history(
    strategy_id: str,
    db_path: str | os.PathLike[str] | None = None,
    strategy_version: str | None = None,
) -> list[dict[str, Any]]:
    """Return all validation attempts for a strategy in chronological order."""
    initialize_graveyard(db_path)
    query = """SELECT run_id, strategy_id, strategy_version, status,
                      evidence_reference, report_json, created_at
               FROM validation_runs WHERE strategy_id = ?"""
    args: list[Any] = [strategy_id]
    if strategy_version is not None:
        query += " AND strategy_version = ?"
        args.append(strategy_version)
    query += " ORDER BY run_id ASC"
    with _connect(db_path) as conn:
        rows = conn.execute(query, args).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["report"] = json.loads(item.pop("report_json"))
        result.append(item)
    return result


def get_run_events(
    run_id: int,
    db_path: str | os.PathLike[str] | None = None,
) -> list[dict[str, Any]]:
    """Return the audit events for one persisted validation run."""
    initialize_graveyard(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT event_id, run_id, event_type, note, created_at
               FROM graveyard_events WHERE run_id = ? ORDER BY event_id ASC""",
            (int(run_id),),
        ).fetchall()
    return [dict(row) for row in rows]
