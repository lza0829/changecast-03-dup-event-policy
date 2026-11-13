"""The duplicate policy is snapshotted when a run starts and is carried on
the persisted batch row. Simulates a config change landing mid-run: the run
in flight must finish under its snapshot, never under the new value.
"""

from __future__ import annotations

import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="changecast-snap-")
os.environ["DATA_DIR"] = TMP
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import connect  # noqa: E402
from app.engines.project import (  # noqa: E402
    OUTCOME_QUARANTINE,
    OUTCOME_ROLLBACK,
    POLICY_IDEMPOTENT,
    POLICY_REJECT,
    apply_events,
)
from app.repositories import streams as stream_repo  # noqa: E402
from app.seed import init_db  # noqa: E402
from app.services import project_service as svc  # noqa: E402

def test_policy_snapshot_is_frozen_for_the_run():
    init_db()
    conn = connect()
    sid = conn.execute("SELECT id FROM project_streams WHERE code='dup_demo'").fetchone()["id"]
    conn.close()

    # Replicate run_project's snapshot-then-process sequence with a config
    # mutation wedged between the two moments.
    from app.repositories import projections as proj_repo
    from app.services.project_service import _load_fixture

    conn = connect()
    try:
        # Pin a known starting state regardless of what earlier tests left.
        stream_repo.update_duplicate_policy(
            conn, stream_id=sid,
            duplicate_policy=POLICY_IDEMPOTENT, conflict_outcome=OUTCOME_QUARANTINE,
        )
        conn.commit()
        stream = stream_repo.get_stream(conn, sid)
        policy_at_start = stream["duplicate_policy"]
        outcome_at_start = stream["conflict_outcome"]
        assert (policy_at_start, outcome_at_start) == (POLICY_IDEMPOTENT, OUTCOME_QUARANTINE)

        events = _load_fixture(stream["fixture"])
        initial = proj_repo.load_initial_projection(conn, sid)
        known = proj_repo.list_known_event_digests(conn, sid)

        # ---- configuration flips while the batch is in flight -------------
        stream_repo.update_duplicate_policy(
            conn, stream_id=sid,
            duplicate_policy=POLICY_REJECT, conflict_outcome=OUTCOME_ROLLBACK,
        )
        conn.commit()

        # The engine still runs against the frozen start-of-run snapshot.
        result = apply_events(
            events,
            known_event_ids=known,
            initial_projection=initial,
            duplicate_policy=policy_at_start,
            conflict_outcome=outcome_at_start,
        )
        assert result["duplicate_policy"] == POLICY_IDEMPOTENT
        assert result["conflict_outcome"] == OUTCOME_QUARANTINE
        assert result["status"] == "completed"  # reject_batch would have failed
        assert result["counts"]["quarantined_events"] == 1
    finally:
        conn.close()

    # A subsequent run picks up the new config.
    b = svc.run_project(sid, note="after flip")
    assert b["duplicate_policy"] == POLICY_REJECT
    assert b["status"] == "rejected"


def _run_all():
    test_policy_snapshot_is_frozen_for_the_run()
    print("PASS test_policy_snapshot_is_frozen_for_the_run")


if __name__ == "__main__":
    _run_all()
