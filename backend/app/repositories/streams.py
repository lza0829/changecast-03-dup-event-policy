from __future__ import annotations

import json
from typing import Any

import sqlite3

from app.engines.project import VALID_CONFLICT_OUTCOMES, VALID_POLICIES


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    d["payload_fields"] = json.loads(d.pop("payload_fields_json"))
    d["enabled"] = bool(d["enabled"])
    return d


def list_streams(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM project_streams ORDER BY id").fetchall()
    return [_row(r) for r in rows]  # type: ignore[misc]


def get_stream(conn: sqlite3.Connection, stream_id: int) -> dict[str, Any] | None:
    return _row(conn.execute("SELECT * FROM project_streams WHERE id=?", (stream_id,)).fetchone())


def set_watermark(conn: sqlite3.Connection, stream_id: int, watermark_seq: int) -> None:
    conn.execute(
        "UPDATE project_streams SET watermark_seq=? WHERE id=?",
        (watermark_seq, stream_id),
    )


def update_duplicate_policy(
    conn: sqlite3.Connection,
    *,
    stream_id: int,
    duplicate_policy: str,
    conflict_outcome: str,
) -> None:
    """Persist the mutually-exclusive duplicate strategy for a stream.

    Validation lives here too so an unknown value can never be stored and
    later be snapshotted into a run.
    """
    if duplicate_policy not in VALID_POLICIES:
        raise ValueError(f"unknown duplicate_policy: {duplicate_policy}")
    if conflict_outcome not in VALID_CONFLICT_OUTCOMES:
        raise ValueError(f"unknown conflict_outcome: {conflict_outcome}")
    conn.execute(
        """
        UPDATE project_streams
        SET duplicate_policy=?, conflict_outcome=?
        WHERE id=?
        """,
        (duplicate_policy, conflict_outcome, stream_id),
    )
