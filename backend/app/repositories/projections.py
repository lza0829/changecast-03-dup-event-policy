from __future__ import annotations

import json
from typing import Any

import sqlite3

from app.engines.digest import payload_digest


def replace_stream_projection(
    conn: sqlite3.Connection,
    *,
    stream_id: int,
    rows: list[dict[str, Any]],
    updated_at: str,
) -> None:
    conn.execute("DELETE FROM projection_rows WHERE stream_id=?", (stream_id,))
    for row in rows:
        conn.execute(
            """
            INSERT INTO projection_rows(
                stream_id, entity_key, payload_json, applied_seq, applied_event_id, deleted, updated_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                stream_id,
                row["entity_key"],
                json.dumps(row.get("payload"), ensure_ascii=False) if row.get("payload") is not None else None,
                row["applied_seq"],
                row["applied_event_id"],
                1 if row.get("deleted") else 0,
                updated_at,
            ),
        )


def list_projections(
    conn: sqlite3.Connection,
    *,
    stream_id: int | None = None,
    include_deleted: bool = True,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM projection_rows WHERE 1=1"
    params: list[Any] = []
    if stream_id is not None:
        sql += " AND stream_id=?"
        params.append(stream_id)
    if not include_deleted:
        sql += " AND deleted=0"
    sql += " ORDER BY stream_id, entity_key"
    out: list[dict[str, Any]] = []
    for r in conn.execute(sql, params).fetchall():
        d = dict(r)
        payload = json.loads(d.pop("payload_json")) if d["payload_json"] else None
        d["payload"] = payload
        d.pop("payload_json", None)
        d["deleted"] = bool(d["deleted"])
        out.append(d)
    return out


def load_initial_projection(
    conn: sqlite3.Connection, stream_id: int
) -> dict[str, dict[str, Any]]:
    """Pre-run projection of a stream, keyed by entity_key, in the shape the
    engine expects. Used so a rejected run can return to exactly this state.
    """
    rows = list_projections(conn, stream_id=stream_id, include_deleted=True)
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        out[r["entity_key"]] = {
            "entity_key": r["entity_key"],
            "payload": r["payload"],
            "applied_seq": r["applied_seq"],
            "applied_event_id": r["applied_event_id"],
            "deleted": r["deleted"],
            "payload_digest": payload_digest(r["payload"]),
        }
    return out


def list_known_event_digests(conn: sqlite3.Connection, stream_id: int) -> dict[str, str]:
    """event_id -> payload digest from prior batches' applied events.

    Only entries carrying a digest count; legacy rows without one fall back
    to a recomputed digest from the current projection when possible.
    """
    known: dict[str, str] = {}
    rows = conn.execute(
        """
        SELECT event_id, payload_digest
        FROM applied_events
        WHERE stream_id=? AND payload_digest IS NOT NULL
        ORDER BY id ASC
        """,
        (stream_id,),
    ).fetchall()
    for r in rows:
        # First occurrence wins; later rows are duplicates/conflicts.
        known.setdefault(r["event_id"], r["payload_digest"])
    return known


def list_legacy_applied_event_ids(conn: sqlite3.Connection, stream_id: int) -> set[str]:
    """event_ids recorded before digests existed (upgraded databases).

    The service recomputes their digests from the fixture's first
    occurrence so cross-run duplicate detection still works after upgrade.
    """
    rows = conn.execute(
        "SELECT DISTINCT event_id FROM applied_events "
        "WHERE stream_id=? AND payload_digest IS NULL",
        (stream_id,),
    ).fetchall()
    return {r["event_id"] for r in rows}


def list_ooo_buffer(conn: sqlite3.Connection, stream_id: int | None = None) -> list[dict[str, Any]]:
    if stream_id is None:
        rows = conn.execute("SELECT * FROM ooo_buffer ORDER BY id DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ooo_buffer WHERE stream_id=? ORDER BY id DESC",
            (stream_id,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload_json"]) if d["payload_json"] else None
        d.pop("payload_json", None)
        out.append(d)
    return out
