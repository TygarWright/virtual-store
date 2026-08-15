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


CORE_EVENT_SPECS = {
    "order.paid": "order",
    "order.delivered": "order",
    "inventory.decremented": "product",
    "refund.initiated": "refund",
    "refund.processed": "refund",
    "refund.failed": "refund",
    "governance.approval.requested": "approval",
    "governance.approval.approved": "approval",
    "governance.approval.rejected": "approval",
    "governance.approval.expired": "approval",
    "governance.approval_policy.updated": "approval_policy",
    "governance.exception.created": "exception",
    "governance.exception.assigned": "exception",
    "governance.exception.acknowledged": "exception",
    "governance.exception.escalated": "exception",
    "governance.exception.resolved": "exception",
    "governance.exception.reopened": "exception",
    "team.message.created": "team_message",
}

def validate_business_event(*, topic: str, aggregate: str, payload: Any) -> None:
    topic = str(topic).strip()
    aggregate = str(aggregate).strip()
    if not topic or not aggregate:
        raise ValueError("event topic and aggregate are required")
    if len(topic) > 180 or len(aggregate) > 100:
        raise ValueError("event topic or aggregate is too long")
    if topic in CORE_EVENT_SPECS and CORE_EVENT_SPECS[topic] != aggregate:
        raise ValueError(f"event aggregate mismatch for {topic}: expected {CORE_EVENT_SPECS[topic]}")
    if not (topic in CORE_EVENT_SPECS or topic.startswith("custom.")):
        raise ValueError(f"unregistered business event topic: {topic}")
    if payload is None:
        return
    canonical_json(payload)

def publish_event(conn, *, topic: str, aggregate: str, aggregate_id: str | int,
                  payload: Any, event_id: str | None = None) -> str:
    """Persist a durable domain event and an outbox delivery record atomically.

    The event is the business fact; the outbox is the delivery mechanism.
    """
    import uuid
    validate_business_event(topic=topic, aggregate=aggregate, payload=payload)
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


def publish_business_event(conn, *, topic: str, aggregate: str, aggregate_id: str | int, payload: Any, event_id: str | None = None) -> str:
    """Canonical domain-event publisher for business modules."""
    return publish_event(conn, topic=topic, aggregate=aggregate, aggregate_id=aggregate_id, payload=payload, event_id=event_id)


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

# Round 42: event-spine delivery/handler primitives.
def list_domain_events(conn, *, topic: str = '', aggregate: str = '', aggregate_id: str = '',
                       since: str = '', limit: int = 200):
    clauses, params = [], []
    for column, value in (("topic", topic), ("aggregate", aggregate), ("aggregate_id", aggregate_id)):
        if value:
            clauses.append(f"{column}=?"); params.append(str(value))
    if since:
        clauses.append("created_at>=?"); params.append(str(since))
    clauses.append("1=1")
    params.append(max(1, min(int(limit), 500)))
    return conn.execute(
        "SELECT event_id, topic, aggregate, aggregate_id, payload_json, created_at "
        "FROM domain_events WHERE " + " AND ".join(clauses) + " ORDER BY created_at DESC LIMIT ?",
        tuple(params),
    ).fetchall()


def record_event_delivery(conn, *, event_id: str, consumer: str, status: str = 'processed',
                          error: str = '', max_attempts: int = 5, available_at: str | None = None) -> None:
    """Record a consumer delivery attempt without hiding retry/dead-letter state."""
    import database as db
    db.ensure_round43_schema(conn)
    now = now_iso()
    existing = conn.execute(
        "SELECT attempts FROM domain_event_deliveries WHERE event_id=? AND consumer=?",
        (str(event_id), str(consumer)),
    ).fetchone()
    attempts = int(existing[0]) + 1 if existing else 1
    if available_at:
        next_available = available_at
    elif str(status) == 'failed':
        delay_minutes = min(30, 2 ** max(0, attempts - 1))
        next_available = (datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)).isoformat()
    else:
        next_available = now
    final_status = str(status)
    if final_status not in {'pending', 'processing', 'processed', 'failed', 'dead_letter'}:
        final_status = 'failed'
    if existing:
        current = conn.execute("SELECT status FROM domain_event_deliveries WHERE event_id=? AND consumer=?", (str(event_id), str(consumer))).fetchone()
        if current and str(current[0]) == 'dead_letter' and final_status != 'pending':
            raise RuntimeError('dead-letter delivery requires explicit requeue before processing')
    if final_status == 'failed' and attempts >= max(1, int(max_attempts)):
        final_status = 'dead_letter'
    delivered_at = now if final_status == 'processed' else None
    conn.execute(
        """INSERT INTO domain_event_deliveries
           (event_id,consumer,status,attempts,last_error,updated_at,available_at,max_attempts,delivered_at)
           VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(event_id,consumer) DO UPDATE SET
             status=excluded.status, attempts=excluded.attempts, last_error=excluded.last_error,
             updated_at=excluded.updated_at, available_at=excluded.available_at,
             max_attempts=excluded.max_attempts, delivered_at=excluded.delivered_at""",
        (str(event_id), str(consumer), final_status, attempts, str(error)[:1000], now, next_available, max(1, int(max_attempts)), delivered_at),
    )

def retryable_event_deliveries(conn, *, consumer: str = '', limit: int = 100):
    import database as db
    db.ensure_round43_schema(conn)
    now = now_iso()
    clauses=["d.status IN ('failed','pending')", "(d.available_at IS NULL OR d.available_at <= ?)"]
    params=[now]
    if consumer:
        clauses.append('d.consumer=?'); params.append(str(consumer))
    params.append(max(1, min(int(limit), 500)))
    return conn.execute(
        "SELECT d.*, e.topic, e.aggregate, e.aggregate_id, e.payload_json FROM domain_event_deliveries d JOIN domain_events e ON e.event_id=d.event_id WHERE " + ' AND '.join(clauses) + " ORDER BY d.updated_at ASC LIMIT ?",
        tuple(params),
    ).fetchall()

def dead_letter_event_deliveries(conn, *, limit: int = 100):
    import database as db
    db.ensure_round43_schema(conn)
    return conn.execute(
        "SELECT d.*, e.topic, e.aggregate, e.aggregate_id FROM domain_event_deliveries d JOIN domain_events e ON e.event_id=d.event_id WHERE d.status='dead_letter' ORDER BY d.updated_at DESC LIMIT ?",
        (max(1, min(int(limit), 500)),),
    ).fetchall()


def requeue_event_delivery(conn, *, event_id: str, consumer: str) -> bool:
    """Explicitly requeue a dead-letter/failed event; never resurrect it implicitly."""
    import database as db
    db.ensure_round43_schema(conn)
    row = conn.execute("SELECT status FROM domain_event_deliveries WHERE event_id=? AND consumer=?", (str(event_id), str(consumer))).fetchone()
    if not row or str(row[0]) not in {"dead_letter", "failed"}:
        return False
    now = now_iso()
    conn.execute("UPDATE domain_event_deliveries SET status='pending', available_at=?, last_error='', updated_at=? WHERE event_id=? AND consumer=?", (now, now, str(event_id), str(consumer)))
    conn.commit()
    return True


def event_spine_contract_report() -> dict[str, Any]:
    return {
        'ok': bool(CORE_EVENT_SPECS) and all('.' in topic and aggregate for topic, aggregate in CORE_EVENT_SPECS.items()),
        'topics': sorted(CORE_EVENT_SPECS),
        'count': len(CORE_EVENT_SPECS),
    }
