from __future__ import annotations

from typing import Any

import sqlite3


def insert_batch(
    conn: sqlite3.Connection,
    *,
    batch_no: str,
    stream_id: int,
    status: str,
    counts: dict[str, int],
    note: str | None,
    created_at: str,
    duplicate_policy: str | None = None,
    conflict_outcome: str | None = None,
    reject_reason: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO project_batches(
            batch_no, stream_id, status, source_events, applied_upsert, applied_delete,
            skipped_duplicate, projected_alive, projected_deleted, max_seq, note, created_at,
            payload_conflicts, batch_rejections, quarantined_events,
            duplicate_policy, conflict_outcome, reject_reason
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            batch_no,
            stream_id,
            status,
            counts["source_events"],
            counts["applied_upsert"],
            counts["applied_delete"],
            counts["skipped_duplicate"],
            counts["projected_alive"],
            counts["projected_deleted"],
            counts["max_seq"],
            note,
            created_at,
            counts.get("payload_conflicts", 0),
            counts.get("batch_rejections", 0),
            counts.get("quarantined_events", 0),
            duplicate_policy,
            conflict_outcome,
            reject_reason,
        ),
    )
    return int(cur.lastrowid)


def list_batches(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM project_batches ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM project_batches WHERE id=?", (batch_id,)).fetchone()
    return dict(r) if r else None


def count_rejected_batches(conn: sqlite3.Connection, stream_id: int | None = None) -> int:
    if stream_id is None:
        r = conn.execute(
            "SELECT COUNT(*) c FROM project_batches WHERE status='rejected'"
        ).fetchone()
    else:
        r = conn.execute(
            "SELECT COUNT(*) c FROM project_batches WHERE status='rejected' AND stream_id=?",
            (stream_id,),
        ).fetchone()
    return int(r["c"])


def insert_applied_event(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    stream_id: int,
    event: dict[str, Any],
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO applied_events(
            batch_id, stream_id, event_id, entity_key, op, seq, event_time, result,
            created_at, payload_digest, prior_digest
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            batch_id,
            stream_id,
            event["event_id"],
            event["entity_key"],
            event["op"],
            event["seq"],
            event["event_time"],
            event["result"],
            created_at,
            event.get("payload_digest"),
            event.get("prior_digest"),
        ),
    )


def list_applied_events(
    conn: sqlite3.Connection,
    *,
    batch_id: int | None = None,
    stream_id: int | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM applied_events WHERE 1=1"
    params: list[Any] = []
    if batch_id is not None:
        sql += " AND batch_id=?"
        params.append(batch_id)
    if stream_id is not None:
        sql += " AND stream_id=?"
        params.append(stream_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]
