"""Canonical payload digest used to tell same-payload redelivery from a
genuine conflict (same event_id, different body).

Hashing is key-order independent and stable across runs/interpreters:
json.dumps with sort_keys and compact separators, then SHA-256 hex.
A null payload (tombstone delete) hashes to a fixed sentinel digest.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

NULL_PAYLOAD_DIGEST = hashlib.sha256(b"\x00null").hexdigest()


def payload_digest(payload: Any) -> str:
    if payload is None:
        return NULL_PAYLOAD_DIGEST
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def short_digest(digest: str | None) -> str:
    if not digest:
        return "—"
    return digest[:10]
