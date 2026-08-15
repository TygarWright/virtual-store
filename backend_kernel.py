"""TITAN backend kernel: small, framework-agnostic primitives inspired by
mature commerce systems: domain events, idempotency records and provider-safe
execution.  These keep the original Virtual Store backend simple while giving
TITAN explicit contracts for cross-domain operations.
"""
from __future__ import annotations

import json
import hashlib
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Callable


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def idempotency_fingerprint(namespace: str, key: str, payload: Any) -> str:
    raw = f"{namespace}|{key}|{canonical_json(payload)}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def begin_idempotent_operation(conn, *, namespace: str, key: str, payload: Any,
                               ttl_seconds: int = 86400) -> dict[str, Any]:
    """Claim an idempotency key or return the previously recorded result.

    Returns {state: 'claimed'|'complete'|'in_progress'|'conflict', ...}.
    """
    if not key:
        raise ValueError("idempotency key is required")
    fingerprint = idempotency_fingerprint(namespace, key, payload)
    now = now_iso()
    row = conn.execute(
        "SELECT * FROM idempotency_keys WHERE namespace=? AND idempotency_key=?",
        (namespace, key),
    ).fetchone()
    if row:
        if row["request_hash"] != fingerprint:
            return {"state": "conflict", "fingerprint": fingerprint}
        if row["status"] == "complete":
            return {"state": "complete", "result": row["result_json"] or "{}", "fingerprint": fingerprint}
        expires = row["expires_at"]
        if expires and expires < now:
            conn.execute(
                "UPDATE idempotency_keys SET status='processing', request_hash=?, expires_at=?, updated_at=? WHERE id=?",
                (fingerprint, _expires_at(ttl_seconds), now, row["id"]),
            )
            return {"state": "claimed", "fingerprint": fingerprint}
        return {"state": "in_progress", "fingerprint": fingerprint}
    try:
        conn.execute(
            "INSERT INTO idempotency_keys(namespace,idempotency_key,request_hash,status,result_json,expires_at,created_at,updated_at) VALUES(?,?,?,'processing','{}',?,?,?)",
            (namespace, key, fingerprint, _expires_at(ttl_seconds), now, now),
        )
        return {"state": "claimed", "fingerprint": fingerprint}
    except Exception as exc:
        # Another worker may have won the race between our SELECT and INSERT.
        # Re-read and resolve deterministically instead of returning a 500.
        message = str(exc).lower()
        if "unique" not in message and "constraint" not in message:
            raise
        row = conn.execute(
            "SELECT * FROM idempotency_keys WHERE namespace=? AND idempotency_key=?",
            (namespace, key),
        ).fetchone()
        if not row:
            raise
        if row["request_hash"] != fingerprint:
            return {"state": "conflict", "fingerprint": fingerprint}
        if row["status"] == "complete":
            return {"state": "complete", "result": row["result_json"] or "{}", "fingerprint": fingerprint}
        return {"state": "in_progress", "fingerprint": fingerprint}


def finish_idempotent_operation(conn, *, namespace: str, key: str, result: Any) -> None:
    now = now_iso()
    conn.execute(
        "UPDATE idempotency_keys SET status='complete', result_json=?, updated_at=? WHERE namespace=? AND idempotency_key=?",
        (canonical_json(result), now, namespace, key),
    )


def fail_idempotent_operation(conn, *, namespace: str, key: str, error: str) -> None:
    now = now_iso()
    conn.execute(
        "UPDATE idempotency_keys SET status='failed', result_json=?, updated_at=? WHERE namespace=? AND idempotency_key=?",
        (canonical_json({"error": str(error)[:500]}), now, namespace, key),
    )


def publish_event(conn, *, topic: str, aggregate: str, aggregate_id: str | int,
                  payload: Any, event_id: str | None = None) -> str:
    """Persist a durable domain event and an outbox delivery record atomically.

    The event is the business fact; the outbox is the delivery mechanism.
    """
    import uuid
    event_id = event_id or str(uuid.uuid4())
    raw = canonical_json(payload)
    created = now_iso()
    conn.execute(
        "INSERT OR IGNORE INTO domain_events(event_id,topic,aggregate,aggregate_id,payload_json,created_at) VALUES(?,?,?,?,?,?)",
        (event_id, topic, aggregate, str(aggregate_id), raw, created),
    )
    conn.execute(
        "INSERT OR IGNORE INTO outbox_jobs(job_type,payload_json,idempotency_key,status,attempts,max_attempts,available_at,created_at,updated_at) VALUES(?,?,?,'pending',0,5,?,?,?)",
        (f"event.{topic}", raw, f"domain-event:{event_id}", created, created, created),
    )
    return event_id


def cleanup_expired_idempotency(conn, *, limit: int = 500) -> int:
    now = now_iso()
    cur = conn.execute(
        "DELETE FROM idempotency_keys WHERE expires_at IS NOT NULL AND expires_at < ? LIMIT ?",
        (now, int(limit)),
    )
    return int(cur.rowcount or 0)


def _expires_at(ttl_seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(1, int(ttl_seconds)))).isoformat()


def measure_provider_call(fn: Callable[..., Any], *args, **kwargs):
    """Execute a provider call and return (result, duration_seconds)."""
    started = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, time.perf_counter() - started
