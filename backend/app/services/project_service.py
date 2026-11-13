from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.config import FIXTURES_DIR
from app.db import connect
from app.engines.digest import payload_digest
from app.engines.project import (
    OUTCOME_QUARANTINE,
    RES_BATCH_REJECTED,
    apply_events,
)
from app.repositories import batches as batch_repo
from app.repositories import conflicts as conflict_repo
from app.repositories import projections as proj_repo
from app.repositories import streams as stream_repo


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_fixture(name: str) -> list[dict[str, Any]]:
    path = FIXTURES_DIR / name
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"fixture {name} must be a JSON array")
    return data


def dashboard_stats() -> dict[str, Any]:
    conn = connect()
    try:
        return {
            "stream_count": conn.execute("SELECT COUNT(*) c FROM project_streams").fetchone()["c"],
            "enabled_streams": conn.execute(
                "SELECT COUNT(*) c FROM project_streams WHERE enabled=1"
            ).fetchone()["c"],
            "batch_count": conn.execute("SELECT COUNT(*) c FROM project_batches").fetchone()["c"],
            "rejected_batches": batch_repo.count_rejected_batches(conn),
            "payload_conflicts": conn.execute(
                "SELECT COALESCE(SUM(payload_conflicts),0) c FROM project_batches"
            ).fetchone()["c"],
            "quarantined_events": conn.execute(
                "SELECT COUNT(*) c FROM conflict_events WHERE outcome=?",
                (OUTCOME_QUARANTINE,),
            ).fetchone()["c"],
            "skipped_duplicates": conn.execute(
                "SELECT COALESCE(SUM(skipped_duplicate),0) c FROM project_batches"
            ).fetchone()["c"],
            "projection_alive": conn.execute(
                "SELECT COUNT(*) c FROM projection_rows WHERE deleted=0"
            ).fetchone()["c"],
            "projection_deleted": conn.execute(
                "SELECT COUNT(*) c FROM projection_rows WHERE deleted=1"
            ).fetchone()["c"],
            "ooo_buffered": conn.execute("SELECT COUNT(*) c FROM ooo_buffer").fetchone()["c"],
            "apply_order": conn.execute(
                "SELECT value FROM settings WHERE key='apply_order'"
            ).fetchone()["value"],
        }
    finally:
        conn.close()


def list_streams() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return stream_repo.list_streams(conn)
    finally:
        conn.close()


def list_batches(limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = batch_repo.list_batches(conn, limit=limit)
        streams = {s["id"]: s for s in stream_repo.list_streams(conn)}
        for r in rows:
            s = streams.get(r["stream_id"])
            r["stream_code"] = s["code"] if s else None
            r["stream_title"] = s["title"] if s else None
        return rows
    finally:
        conn.close()


def list_projections(stream_id: int | None = None, include_deleted: bool = True) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = proj_repo.list_projections(
            conn, stream_id=stream_id, include_deleted=include_deleted
        )
        streams = {s["id"]: s for s in stream_repo.list_streams(conn)}
        for r in rows:
            s = streams.get(r["stream_id"])
            r["stream_code"] = s["code"] if s else None
            r["stream_title"] = s["title"] if s else None
        return rows
    finally:
        conn.close()


def list_events(batch_id: int | None = None, stream_id: int | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return batch_repo.list_applied_events(conn, batch_id=batch_id, stream_id=stream_id)
    finally:
        conn.close()


def list_conflicts(stream_id: int | None = None, batch_id: int | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = conflict_repo.list_conflict_events(
            conn, stream_id=stream_id, batch_id=batch_id
        )
        streams = {s["id"]: s for s in stream_repo.list_streams(conn)}
        for r in rows:
            s = streams.get(r["stream_id"])
            r["stream_code"] = s["code"] if s else None
            r["stream_title"] = s["title"] if s else None
        return rows
    finally:
        conn.close()


def list_ooo_buffer(stream_id: int | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return proj_repo.list_ooo_buffer(conn, stream_id=stream_id)
    finally:
        conn.close()


def list_settings() -> dict[str, str]:
    conn = connect()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def update_stream_policy(
    stream_id: int, duplicate_policy: str, conflict_outcome: str
) -> dict[str, Any]:
    conn = connect()
    try:
        stream = stream_repo.get_stream(conn, stream_id)
        if not stream:
            raise ValueError("stream not found")
        stream_repo.update_duplicate_policy(
            conn,
            stream_id=stream_id,
            duplicate_policy=duplicate_policy,
            conflict_outcome=conflict_outcome,
        )
        conn.commit()
        updated = stream_repo.get_stream(conn, stream_id)
        assert updated is not None
        return updated
    finally:
        conn.close()


def run_project(stream_id: int, note: str | None = None) -> dict[str, Any]:
    """Load CDC fixture and project it under the stream's duplicate policy.

    The policy is read and frozen before any event is processed, so a config
    change made while the batch is in flight cannot alter this run's result.
    A rejected run is committed only as ledger rows (batch + applied log +
    conflict trail); the projection is left byte-identical to its pre-run
    snapshot. Everything happens in one transaction — no half success.
    """
    conn = connect()
    try:
        stream = stream_repo.get_stream(conn, stream_id)
        if not stream:
            raise ValueError("stream not found")
        if not stream["enabled"]:
            raise ValueError("stream disabled")

        # --- frozen policy snapshot for this run ---------------------------
        duplicate_policy = stream["duplicate_policy"]
        conflict_outcome = stream["conflict_outcome"]

        events = _load_fixture(stream["fixture"])
        initial_projection = proj_repo.load_initial_projection(conn, stream_id)
        known_digests = proj_repo.list_known_event_digests(conn, stream_id)

        # Upgraded databases may carry pre-feature applied_events without
        # digests. Reconstruct their digests from the fixture's first
        # occurrence (the body that was actually applied), so cross-run
        # duplicates are still detected instead of silently re-applying.
        legacy_ids = proj_repo.list_legacy_applied_event_ids(conn, stream_id)
        if legacy_ids:
            fixture_first: dict[str, str] = {}
            for ev in events:
                eid = str(ev["event_id"])
                fixture_first.setdefault(eid, payload_digest(ev.get("payload")))
            for eid in legacy_ids:
                digest = fixture_first.get(eid)
                if digest is not None:
                    known_digests.setdefault(eid, digest)

        result = apply_events(
            events,
            known_event_ids=known_digests,
            initial_projection=initial_projection,
            duplicate_policy=duplicate_policy,
            conflict_outcome=conflict_outcome,
        )

        now = _now()
        seq = conn.execute("SELECT COUNT(*) c FROM project_batches").fetchone()["c"] + 1
        batch_no = f"PB-{now[:10].replace('-', '')}-{seq:04d}"
        rejected = result["status"] == "rejected"

        batch_id = batch_repo.insert_batch(
            conn,
            batch_no=batch_no,
            stream_id=stream_id,
            status=result["status"],
            counts=result["counts"],
            note=note,
            created_at=now,
            duplicate_policy=duplicate_policy,
            conflict_outcome=conflict_outcome,
            reject_reason=result["reject_reason"],
        )

        # Ledger trail is written for every run, including rejected ones,
        # so 跳过数/冲突数/拒绝次数 can be reconciled against projection state.
        for ev in result["applied_log"]:
            batch_repo.insert_applied_event(
                conn, batch_id=batch_id, stream_id=stream_id, event=ev, created_at=now
            )
        for ev in result["conflict_log"]:
            outcome = RES_BATCH_REJECTED if rejected else OUTCOME_QUARANTINE
            conflict_repo.insert_conflict_event(
                conn,
                stream_id=stream_id,
                batch_id=batch_id,
                event=ev,
                outcome=outcome,
                created_at=now,
            )

        if rejected:
            # Whole-batch failure: keep the pre-run projection untouched.
            # No watermark advance — nothing from this run was applied.
            conn.commit()
        else:
            proj_repo.replace_stream_projection(
                conn, stream_id=stream_id, rows=result["projection"], updated_at=now
            )
            stream_repo.set_watermark(conn, stream_id, result["counts"]["max_seq"])
            conn.commit()

        batch = batch_repo.get_batch(conn, batch_id)
        assert batch is not None
        batch["stream_code"] = stream["code"]
        batch["stream_title"] = stream["title"]
        batch["apply_order"] = "by_seq"
        batch["rejected_event_id"] = result["rejected_event_id"]
        batch["counts"] = result["counts"]
        if rejected:
            # Pre-run snapshot proves the projection did not move.
            batch["projection_preview"] = proj_repo.list_projections(conn, stream_id=stream_id)[:20]
        else:
            batch["projection_preview"] = proj_repo.list_projections(conn, stream_id=stream_id)[:20]
        batch["event_preview"] = batch_repo.list_applied_events(conn, batch_id=batch_id, limit=20)
        batch["conflict_preview"] = conflict_repo.list_conflict_events(
            conn, batch_id=batch_id, limit=20
        )
        return batch
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
