"""Apply CDC events onto a projection by seq order, with explicit,
mutually-exclusive duplicate-event policies.

Policies (read once at run start and passed in as a frozen snapshot):

* ``idempotent`` — a repeated event_id whose payload digest matches the
  first occurrence is skipped as a duplicate and never touches the
  projection a second time. A repeated event_id with a *different* digest
  is a conflict and is handled per ``conflict_outcome``:
    - ``rollback_batch``: the whole batch is rejected, projection rolls
      back to the state before this run;
    - ``quarantine``: the conflicting event is isolated (excluded from the
      projection) while every other event of the run still applies.
* ``reject_batch`` — any repeated event_id rejects the entire batch
  regardless of payload digest; projection returns to its pre-run state.

The engine is pure: it builds on a caller-supplied pre-run projection and
known-event map, and returns a full result envelope. Persistence and
transaction boundaries live in the service layer, so a rejected run never
leaves a half-written projection.
"""

from __future__ import annotations

from typing import Any

from app.engines.digest import payload_digest

POLICY_IDEMPOTENT = "idempotent"
POLICY_REJECT = "reject_batch"
VALID_POLICIES = (POLICY_IDEMPOTENT, POLICY_REJECT)

OUTCOME_ROLLBACK = "rollback_batch"
OUTCOME_QUARANTINE = "quarantine"
VALID_CONFLICT_OUTCOMES = (OUTCOME_ROLLBACK, OUTCOME_QUARANTINE)

# applied_log / conflict_log result labels
RES_APPLIED_UPSERT = "applied_upsert"
RES_APPLIED_DELETE = "applied_delete"
RES_SKIPPED_DUPLICATE = "skipped_duplicate"
RES_CONFLICT_QUARANTINED = "conflict_quarantined"
RES_BATCH_REJECTED = "batch_rejected"


def _empty_counts() -> dict[str, int]:
    return {
        "source_events": 0,
        "applied_upsert": 0,
        "applied_delete": 0,
        "skipped_duplicate": 0,
        "payload_conflicts": 0,
        "batch_rejections": 0,
        "quarantined_events": 0,
        "max_seq": 0,
        "projected_alive": 0,
        "projected_deleted": 0,
    }


def apply_events(
    events: list[dict[str, Any]],
    *,
    known_event_ids: dict[str, str] | None = None,
    initial_projection: dict[str, dict[str, Any]] | None = None,
    duplicate_policy: str = POLICY_IDEMPOTENT,
    conflict_outcome: str = OUTCOME_ROLLBACK,
) -> dict[str, Any]:
    """Apply events under a frozen duplicate-policy snapshot.

    ``known_event_ids`` maps event_id -> payload digest for events already
    applied in earlier runs (cross-run idempotency). ``initial_projection``
    is the stream's projection before this run, keyed by entity_key; the
    engine never mutates it in place.
    """
    if duplicate_policy not in VALID_POLICIES:
        raise ValueError(f"unknown duplicate_policy: {duplicate_policy}")
    if conflict_outcome not in VALID_CONFLICT_OUTCOMES:
        raise ValueError(f"unknown conflict_outcome: {conflict_outcome}")

    # Digests of first occurrences, seeded from prior runs. Cross-run
    # duplicates are therefore detected exactly like in-run duplicates.
    known: dict[str, str] = dict(known_event_ids or {})
    # Deep-ish copy: rows are rebuilt on write anyway.
    projection: dict[str, dict[str, Any]] = {
        k: dict(v) for k, v in (initial_projection or {}).items()
    }
    applied_log: list[dict[str, Any]] = []
    conflict_log: list[dict[str, Any]] = []
    counts = _empty_counts()
    counts["source_events"] = len(events)

    ordered = sorted(events, key=lambda e: (int(e.get("seq", 0)), str(e.get("event_id", ""))))

    status = "completed"
    reject_reason: str | None = None
    rejected_event_id: str | None = None

    for ev in ordered:
        event_id = str(ev["event_id"])
        entity_key = str(ev["entity_key"])
        op = str(ev.get("op", "upsert"))
        seq = int(ev.get("seq", 0))
        event_time = str(ev.get("event_time", ""))
        digest = payload_digest(ev.get("payload"))
        counts["max_seq"] = max(counts["max_seq"], seq)

        base_entry = {
            "event_id": event_id,
            "entity_key": entity_key,
            "op": op,
            "seq": seq,
            "event_time": event_time,
            "payload": ev.get("payload"),
            "payload_digest": digest,
        }

        if event_id in known:
            prior_digest = known[event_id]
            same_payload = prior_digest == digest

            # Policy B: every duplicate rejects the batch, digest ignored.
            if duplicate_policy == POLICY_REJECT:
                status = "rejected"
                reject_reason = "duplicate_event_id"
                rejected_event_id = event_id
                counts["batch_rejections"] = 1
                applied_log.append(
                    {**base_entry, "result": RES_BATCH_REJECTED, "prior_digest": prior_digest}
                )
                break

            # Policy A: same digest -> idempotent skip, no second touch.
            if same_payload:
                counts["skipped_duplicate"] += 1
                applied_log.append(
                    {**base_entry, "result": RES_SKIPPED_DUPLICATE, "prior_digest": prior_digest}
                )
                continue

            # Policy A, different digest -> payload conflict.
            counts["payload_conflicts"] += 1
            conflict_entry = {
                **base_entry,
                "prior_digest": prior_digest,
                "conflict_kind": "payload_mismatch",
            }
            if conflict_outcome == OUTCOME_ROLLBACK:
                status = "rejected"
                reject_reason = "payload_conflict"
                rejected_event_id = event_id
                counts["batch_rejections"] = 1
                applied_log.append({**conflict_entry, "result": RES_BATCH_REJECTED})
                conflict_log.append({**conflict_entry, "result": RES_BATCH_REJECTED})
                break

            # quarantine: isolate this event; the rest of the run continues.
            counts["quarantined_events"] += 1
            applied_log.append({**conflict_entry, "result": RES_CONFLICT_QUARANTINED})
            conflict_log.append({**conflict_entry, "result": RES_CONFLICT_QUARANTINED})
            continue

        # First time we see this event_id in any run.
        known[event_id] = digest
        if op == "delete":
            projection[entity_key] = {
                "entity_key": entity_key,
                "payload": None,
                "applied_seq": seq,
                "applied_event_id": event_id,
                "deleted": True,
                "payload_digest": digest,
            }
            counts["applied_delete"] += 1
            result = RES_APPLIED_DELETE
        else:
            projection[entity_key] = {
                "entity_key": entity_key,
                "payload": ev.get("payload") or {},
                "applied_seq": seq,
                "applied_event_id": event_id,
                "deleted": False,
                "payload_digest": digest,
            }
            counts["applied_upsert"] += 1
            result = RES_APPLIED_UPSERT

        applied_log.append({**base_entry, "result": result})

    if status == "rejected":
        # Whole-batch rejection: discard every projection mutation made
        # during this run. The caller persists nothing from `projection`.
        final_projection = initial_projection or {}
    else:
        final_projection = projection

    alive = [r for r in final_projection.values() if not r["deleted"]]
    deleted = [r for r in final_projection.values() if r["deleted"]]
    counts["projected_alive"] = len(alive)
    counts["projected_deleted"] = len(deleted)

    return {
        "status": status,
        "reject_reason": reject_reason,
        "rejected_event_id": rejected_event_id,
        "duplicate_policy": duplicate_policy,
        "conflict_outcome": conflict_outcome,
        "counts": counts,
        "projection": list(final_projection.values()),
        "applied_log": applied_log,
        "conflict_log": conflict_log,
    }
