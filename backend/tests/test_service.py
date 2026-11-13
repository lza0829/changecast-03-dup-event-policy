"""Service/DB integration tests for duplicate policy runs.

Runnable with plain stdlib against a throwaway DATA_DIR:
    DATA_DIR=<tmp> python -I tests/test_service.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="changecast-test-")
os.environ["DATA_DIR"] = TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import connect  # noqa: E402
from app.engines.project import (  # noqa: E402
    OUTCOME_QUARANTINE,
    OUTCOME_ROLLBACK,
    POLICY_IDEMPOTENT,
    POLICY_REJECT,
)
from app.repositories import projections as proj_repo  # noqa: E402
from app.seed import init_db  # noqa: E402
from app.services import project_service as svc  # noqa: E402


def _demo_stream_id(conn) -> int:
    r = conn.execute("SELECT id FROM project_streams WHERE code='dup_demo'").fetchone()
    return int(r["id"])


def _projection_snapshot(stream_id: int):
    rows = svc.list_projections(stream_id=stream_id)
    return sorted((r["entity_key"], r["applied_event_id"], r["deleted"],
                   r["payload"]) for r in rows)


def test_full_policy_lifecycle():
    init_db()
    conn = connect()
    sid = _demo_stream_id(conn)
    conn.close()

    # ---- run 1: idempotent + quarantine (seed default) --------------------
    b1 = svc.run_project(sid, note="quarantine run")
    assert b1["status"] == "completed"
    assert b1["duplicate_policy"] == POLICY_IDEMPOTENT
    assert b1["conflict_outcome"] == OUTCOME_QUARANTINE
    assert b1["skipped_duplicate"] == 2
    assert b1["payload_conflicts"] == 1
    assert b1["quarantined_events"] == 1
    assert b1["batch_rejections"] == 0
    snap1 = _projection_snapshot(sid)
    # tampered dd-003 must not have overwritten C01
    c01 = next(r for r in snap1 if r[0] == "C01")
    assert c01[3] == {"level": "A", "owner": "林晓", "note": "升级申请"}
    conflicts = svc.list_conflicts(stream_id=sid)
    assert len(conflicts) == 1
    assert conflicts[0]["event_id"] == "dd-003"
    assert conflicts[0]["outcome"] == OUTCOME_QUARANTINE
    assert conflicts[0]["payload_digest"] != conflicts[0]["prior_digest"]

    # ledger digests are populated for every applied event
    ev_rows = svc.list_events(batch_id=b1["id"])
    assert all(e["payload_digest"] for e in ev_rows)

    # ---- run 2: same config, redelivery -> skips, projection frozen -------
    b2 = svc.run_project(sid, note="redelivery")
    assert b2["status"] == "completed"
    assert b2["skipped_duplicate"] == 8
    assert b2["payload_conflicts"] == 1
    assert _projection_snapshot(sid) == snap1

    # ---- policy switch: idempotent + rollback -> whole batch rejected -----
    svc.update_stream_policy(sid, POLICY_IDEMPOTENT, OUTCOME_ROLLBACK)
    b3 = svc.run_project(sid, note="rollback run")
    assert b3["status"] == "rejected"
    assert b3["reject_reason"] == "payload_conflict"
    assert b3["rejected_event_id"] == "dd-003"
    assert b3["batch_rejections"] == 1
    # no half projection: snapshot unchanged, watermark unchanged
    assert _projection_snapshot(sid) == snap1
    conn = connect()
    wm = conn.execute("SELECT watermark_seq FROM project_streams WHERE id=?", (sid,)).fetchone()
    assert wm["watermark_seq"] == b1["max_seq"]
    conn.close()
    c3 = svc.list_conflicts(batch_id=b3["id"])
    assert c3[0]["outcome"] == "batch_rejected"

    # ---- policy switch: reject_batch -> rejected regardless of digest -----
    svc.update_stream_policy(sid, POLICY_REJECT, OUTCOME_QUARANTINE)
    b4 = svc.run_project(sid, note="strict reject")
    assert b4["status"] == "rejected"
    assert b4["reject_reason"] == "duplicate_event_id"
    # dd-001 was applied in run 1, so on this rerun it is the first
    # cross-run duplicate hit (seq order) — digest is never consulted.
    assert b4["rejected_event_id"] == "dd-001"
    assert b4["skipped_duplicate"] == 0
    assert _projection_snapshot(sid) == snap1  # still untouched

    # ---- snapshot immutability: old batch keeps the policy it ran under ---
    assert b1["duplicate_policy"] == POLICY_IDEMPOTENT
    assert b1["conflict_outcome"] == OUTCOME_QUARANTINE
    assert b3["duplicate_policy"] == POLICY_IDEMPOTENT
    assert b3["conflict_outcome"] == OUTCOME_ROLLBACK
    assert b4["duplicate_policy"] == POLICY_REJECT

    # ---- cross-reconciliation: counters vs projection vs ledger ----------
    stats = svc.dashboard_stats()
    assert stats["rejected_batches"] == 2
    assert stats["quarantined_events"] == 2  # run1 + run2
    # skips: 2 (run1 in-fixture redeliveries) + 8 (run2) + 5 (run3 gets
    # through five same-digest cross-run dups before the seq-6 conflict)
    assert stats["skipped_duplicates"] == 2 + 8 + 5
    assert stats["payload_conflicts"] == 3   # runs 1, 2, 3 (reject never checks)
    batches = svc.list_batches(limit=50)
    demo_batches = [b for b in batches if b["stream_id"] == sid]
    assert sum(b["batch_rejections"] for b in demo_batches) == 2
    # every rejected batch has zero projection writes of its own: the
    # surviving projection is exactly the completed run's output
    assert _projection_snapshot(sid) == snap1


def test_original_fixtures_still_project_cleanly():
    """The three original fixtures contain no duplicate event_ids, so every
    run completes without conflicts or rejection. Cross-run skips are fine
    when earlier tests already applied some of their events.
    """
    conn = connect()
    rows = conn.execute("SELECT id, code FROM project_streams").fetchall()
    conn.close()
    for r in rows:
        if r["code"] == "dup_demo":
            continue
        b = svc.run_project(r["id"], note="baseline")
        assert b["status"] == "completed"
        assert b["batch_rejections"] == 0
        assert b["payload_conflicts"] == 0
        assert b["quarantined_events"] == 0
        # Original fixtures have no in-fixture duplicate event_ids: any skip
        # must be a cross-run redelivery from an earlier test's ledger.
        assert b["applied_upsert"] + b["applied_delete"] + b["skipped_duplicate"] == \
               b["source_events"]


def test_legacy_rows_without_digests_still_detect_cross_run_duplicates():
    """An upgraded database has applied_events rows with NULL digests.
    Re-running the same fixture must skip same-payload redeliveries via the
    fixture-derived fallback digest rather than re-apply them.

    Uses member_profile, which has no ledger rows of its own, so the
    fallback path is genuinely exercised regardless of test ordering.
    """
    from app.engines.digest import payload_digest
    import json as _json

    init_db()
    conn = connect()
    sid = conn.execute("SELECT id FROM project_streams WHERE code='member_profile'").fetchone()["id"]
    now = "2026-01-01T00:00:00Z"
    cur = conn.execute(
        """INSERT INTO project_batches(batch_no, stream_id, status, source_events,
               applied_upsert, applied_delete, skipped_duplicate, projected_alive,
               projected_deleted, max_seq, created_at)
           VALUES ('PB-legacy-mp', ?, 'completed', 1, 1, 0, 0, 1, 0, 1, ?)""",
        (sid, now),
    )
    legacy_batch = cur.lastrowid
    # Pre-feature ledger row: no payload_digest / prior_digest columns populated.
    conn.execute(
        """INSERT INTO applied_events(batch_id, stream_id, event_id, entity_key, op,
               seq, event_time, result, created_at, payload_digest, prior_digest)
           VALUES (?, ?, 'mp-001', 'M1001', 'upsert', 1, ?, 'applied_upsert', ?, NULL, NULL)""",
        (legacy_batch, sid, now, now),
    )
    conn.commit()
    conn.close()

    b = svc.run_project(sid, note="after upgrade")
    assert b["status"] == "completed"
    # Every member_profile event is a cross-run duplicate; none re-applies.
    assert b["applied_upsert"] == 0 and b["applied_delete"] == 0
    assert b["skipped_duplicate"] == b["source_events"]
    # The NULL-digest legacy row is matched via the fixture-derived fallback.
    ev = next(e for e in svc.list_events(batch_id=b["id"]) if e["event_id"] == "mp-001")
    assert ev["result"] == "skipped_duplicate"
    fixture = _json.loads(
        (Path(__file__).resolve().parent.parent / "app" / "fixtures" / "member_profile.json")
        .read_text(encoding="utf-8")
    )
    assert ev["prior_digest"] == payload_digest(fixture[0]["payload"])


def test_invalid_policy_rejected():
    conn = connect()
    sid = _demo_stream_id(conn)
    conn.close()
    try:
        svc.update_stream_policy(sid, "nonsense", OUTCOME_ROLLBACK)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid policy should be rejected")
    # stored config untouched
    streams = {s["id"]: s for s in svc.list_streams()}
    assert streams[sid]["duplicate_policy"] == POLICY_REJECT


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} service tests passed (DATA_DIR={TMP})")


if __name__ == "__main__":
    _run_all()
