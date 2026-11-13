"""Engine-level tests for the duplicate-event policies.

Runnable with plain stdlib:  python -I tests/test_engine.py
(pytest also discovers these functions.)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engines.digest import payload_digest  # noqa: E402
from app.engines.project import (  # noqa: E402
    OUTCOME_QUARANTINE,
    OUTCOME_ROLLBACK,
    POLICY_IDEMPOTENT,
    POLICY_REJECT,
    RES_APPLIED_UPSERT,
    RES_BATCH_REJECTED,
    RES_CONFLICT_QUARANTINED,
    RES_SKIPPED_DUPLICATE,
    apply_events,
)


def _ev(eid, key, seq, payload, op="upsert"):
    return {"event_id": eid, "entity_key": key, "op": op, "seq": seq,
            "event_time": f"2026-04-01T08:{seq:02d}:00Z", "payload": payload}


def test_idempotent_same_payload_skips_and_never_reprojects():
    d1, d2 = {"v": 1}, {"v": 1}
    assert payload_digest(d1) == payload_digest(d2)
    events = [_ev("e1", "K1", 1, {"v": 1}), _ev("e1", "K1", 2, {"v": 1})]
    r = apply_events(events, duplicate_policy=POLICY_IDEMPOTENT,
                     conflict_outcome=OUTCOME_ROLLBACK)
    assert r["status"] == "completed"
    assert r["counts"]["skipped_duplicate"] == 1
    assert r["counts"]["applied_upsert"] == 1
    # second occurrence must not change applied_seq on the projection
    assert r["projection"][0]["applied_seq"] == 1
    results = [e["result"] for e in r["applied_log"]]
    assert results == [RES_APPLIED_UPSERT, RES_SKIPPED_DUPLICATE]


def test_key_order_independent_digest():
    assert payload_digest({"a": 1, "b": 2}) == payload_digest({"b": 2, "a": 1})


def test_idempotent_conflict_rollback_rejects_and_returns_initial_projection():
    initial = {
        "K0": {"entity_key": "K0", "payload": {"old": True}, "applied_seq": 0,
               "applied_event_id": "e0", "deleted": False,
               "payload_digest": payload_digest({"old": True})}
    }
    events = [
        _ev("e1", "K1", 1, {"v": 1}),
        _ev("e1", "K1", 2, {"v": 2}),  # same id, different digest
        _ev("e2", "K2", 3, {"v": 3}),  # would otherwise apply
    ]
    r = apply_events(events, initial_projection=initial,
                     duplicate_policy=POLICY_IDEMPOTENT,
                     conflict_outcome=OUTCOME_ROLLBACK)
    assert r["status"] == "rejected"
    assert r["reject_reason"] == "payload_conflict"
    assert r["counts"]["payload_conflicts"] == 1
    assert r["counts"]["batch_rejections"] == 1
    # projection must be exactly the pre-run snapshot: no K1/K2, K0 intact
    assert {k["entity_key"] for k in r["projection"]} == {"K0"}
    assert r["projection"][0]["payload"] == {"old": True}


def test_idempotent_conflict_quarantine_isolates_and_continues():
    events = [
        _ev("e1", "K1", 1, {"v": 1}),
        _ev("e2", "K2", 2, {"v": 2}),
        _ev("e1", "K1", 3, {"v": 999}),  # conflict -> quarantine
        _ev("e3", "K3", 4, {"v": 3}),    # still applies
    ]
    r = apply_events(events, duplicate_policy=POLICY_IDEMPOTENT,
                     conflict_outcome=OUTCOME_QUARANTINE)
    assert r["status"] == "completed"
    assert r["counts"]["payload_conflicts"] == 1
    assert r["counts"]["quarantined_events"] == 1
    assert r["counts"]["batch_rejections"] == 0
    assert r["counts"]["applied_upsert"] == 3
    # K1 keeps the first payload, the tampered body never reaches projection
    k1 = next(p for p in r["projection"] if p["entity_key"] == "K1")
    assert k1["payload"] == {"v": 1}
    assert {p["entity_key"] for p in r["projection"]} == {"K1", "K2", "K3"}
    assert len(r["conflict_log"]) == 1
    assert r["conflict_log"][0]["result"] == RES_CONFLICT_QUARANTINED


def test_reject_policy_rejects_even_with_same_payload():
    events = [_ev("e1", "K1", 1, {"v": 1}), _ev("e1", "K1", 2, {"v": 1})]
    r = apply_events(events, duplicate_policy=POLICY_REJECT,
                     conflict_outcome=OUTCOME_QUARANTINE)
    assert r["status"] == "rejected"
    assert r["reject_reason"] == "duplicate_event_id"
    assert r["counts"]["batch_rejections"] == 1
    assert r["counts"]["skipped_duplicate"] == 0
    assert r["counts"]["payload_conflicts"] == 0
    assert r["projection"] == []  # nothing applied
    assert r["applied_log"][-1]["result"] == RES_BATCH_REJECTED


def test_cross_run_duplicate_uses_known_digests():
    known = {"e1": payload_digest({"v": 1})}
    initial = {
        "K1": {"entity_key": "K1", "payload": {"v": 1}, "applied_seq": 1,
               "applied_event_id": "e1", "deleted": False,
               "payload_digest": known["e1"]}
    }
    # rerun: same event same body -> skip; projection untouched
    r_same = apply_events(
        [_ev("e1", "K1", 1, {"v": 1})],
        known_event_ids=known, initial_projection=initial,
        duplicate_policy=POLICY_IDEMPOTENT, conflict_outcome=OUTCOME_ROLLBACK,
    )
    assert r_same["counts"]["skipped_duplicate"] == 1
    assert r_same["projection"][0]["applied_seq"] == 1

    # rerun with tampered body -> conflict rollback, projection stays
    r_diff = apply_events(
        [_ev("e1", "K1", 1, {"v": 2})],
        known_event_ids=known, initial_projection=initial,
        duplicate_policy=POLICY_IDEMPOTENT, conflict_outcome=OUTCOME_ROLLBACK,
    )
    assert r_diff["status"] == "rejected"
    assert r_diff["projection"][0]["payload"] == {"v": 1}


def test_demo_fixture_first_run_quarantine_counts():
    import json
    fixture = json.loads(
        (Path(__file__).resolve().parent.parent / "app" / "fixtures" / "dup_demo.json")
        .read_text(encoding="utf-8")
    )
    r = apply_events(fixture, duplicate_policy=POLICY_IDEMPOTENT,
                     conflict_outcome=OUTCOME_QUARANTINE)
    c = r["counts"]
    assert c["source_events"] == 9
    assert c["skipped_duplicate"] == 2          # dd-002 seq4, dd-001 seq9
    assert c["payload_conflicts"] == 1          # dd-003 seq6 tampered
    assert c["quarantined_events"] == 1
    assert c["applied_upsert"] == 5
    assert c["applied_delete"] == 1
    assert c["projected_alive"] == 3   # C01/C03/C04; C02 tombstoned by dd-005
    assert c["projected_deleted"] == 1
    # rerun under same policy: same-digest redeliveries skip; the tampered
    # dd-003 still mismatches its first-occurrence digest and is quarantined
    # again — the conflict is never silently swallowed.
    known: dict[str, str] = {}
    for e in r["applied_log"]:
        if e["result"] in (RES_APPLIED_UPSERT, "applied_delete"):
            known.setdefault(e["event_id"], e["payload_digest"])
    r2 = apply_events(fixture, known_event_ids=known,
                      initial_projection={p["entity_key"]: p for p in r["projection"]},
                      duplicate_policy=POLICY_IDEMPOTENT,
                      conflict_outcome=OUTCOME_QUARANTINE)
    assert r2["counts"]["skipped_duplicate"] == 8
    assert r2["counts"]["payload_conflicts"] == 1
    assert r2["counts"]["quarantined_events"] == 1
    assert r2["counts"]["applied_upsert"] == 0
    assert r2["status"] == "completed"
    # projection identical to run one (same entity states)
    assert sorted(p["entity_key"] for p in r2["projection"]) == \
           sorted(p["entity_key"] for p in r["projection"])


def test_demo_fixture_reject_policy():
    import json
    fixture = json.loads(
        (Path(__file__).resolve().parent.parent / "app" / "fixtures" / "dup_demo.json")
        .read_text(encoding="utf-8")
    )
    r = apply_events(fixture, duplicate_policy=POLICY_REJECT,
                     conflict_outcome=OUTCOME_QUARANTINE)
    assert r["status"] == "rejected"
    assert r["reject_reason"] == "duplicate_event_id"
    assert r["rejected_event_id"] == "dd-002"  # first duplicate in seq order
    assert r["projection"] == []
    assert r["counts"]["batch_rejections"] == 1


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} engine tests passed")


if __name__ == "__main__":
    _run_all()
