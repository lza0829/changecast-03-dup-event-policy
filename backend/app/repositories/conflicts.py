from __future__ import annotations

import json
from typing import Any

import sqlite3


def insert_conflict_event(
    conn: sqlite3.Connection,
    *,
    stream_id: int,
    batch_id: int,
    event: dict[str, Any],
    outcome: str,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO conflict_events(
            stream_id, batch_id, event_id, entity_key, op, seq, event_time,
            payload_json, payload_digest, prior_digest, conflict_kind, outcome, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            stream_id,
            batch_id,
            event["event_id"],
            event["entity_key"],
            event["op"],
            event["seq"],
            event["event_time"],
            json.dumps(event.get("payload"), ensure_ascii=False)
            if event.get("payload") is not None
            else None,
            event["payload_digest"],
            event["prior_digest"],
            event["conflict_kind"],
            outcome,
            created_at,
        ),
    )


def list_conflict_events(
    conn: sqlite3.Connection,
    *,
    stream_id: int | None = None,
    batch_id: int | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM conflict_events WHERE 1=1"
    params: list[Any] = []
    if stream_id is not None:
        sql += " AND stream_id=?"
        params.append(stream_id)
    if batch_id is not None:
        sql += " AND batch_id=?"
        params.append(batch_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    out: list[dict[str, Any]] = []
    for r in conn.execute(sql, params).fetchall():
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json")) if d["payload_json"] else None
        d.pop("payload_json", None)
        out.append(d)
    return out
