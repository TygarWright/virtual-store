#!/usr/bin/env python3
"""Durable Virtual Store outbox worker.

Claims database-backed outbox jobs with a lease, executes supported handlers,
and retries transient failures with bounded backoff. Safe for a single worker
or multiple workers sharing the same SQLite/Turso database.
"""
from __future__ import annotations

import json
import os
import signal
import time
import uuid

import config
import database as db
from phase2_services import claim_outbox_job, complete_outbox_job, retry_outbox_job

STOP = False


def _stop(*_args):
    global STOP
    STOP = True


def _handle_email_send(payload: dict) -> None:
    from helpers import send_email

    to = (payload.get("to") or "").strip()
    subject = payload.get("subject") or ""
    body = payload.get("body") or ""
    if not to or not subject:
        raise ValueError("email.send requires recipient and subject")
    send_email(to, subject, body)


HANDLERS = {
    "email.send": _handle_email_send,
}


def process_one(conn, worker_id: str) -> bool:
    job = claim_outbox_job(conn, worker_id=worker_id, lease_seconds=300)
    if not job:
        return False
    try:
        payload = json.loads(job["payload_json"] or "{}")
        handler = HANDLERS.get(job["job_type"])
        if handler is None:
            raise ValueError(f"Unsupported outbox job type: {job['job_type']}")
        handler(payload)
        complete_outbox_job(conn, job["id"])
        conn.commit()
        return True
    except Exception as exc:
        max_attempts = int(job["max_attempts"] or 5)
        attempts = int(job["attempts"] or 0)
        if attempts >= max_attempts:
            conn.execute(
                "UPDATE outbox_jobs SET status='dead', last_error=?, locked_at=NULL, locked_by=NULL, updated_at=? WHERE id=?",
                (str(exc)[:2000], db.now(), job["id"]),
            )
            conn.commit()
        else:
            retry_outbox_job(conn, job["id"], str(exc), delay_seconds=30, max_delay_seconds=900)
            conn.commit()
        return True


def main() -> int:
    if not config.REDIS_URL and os.environ.get("OUTBOX_WORKER_ENABLED", "").lower() not in {"1", "true", "yes", "on"}:
        # A worker can still run without Redis; this guard prevents accidentally
        # starting an orphan process in development when background processing
        # was not explicitly requested.
        print("OUTBOX_WORKER_ENABLED is not enabled; worker exiting.")
        return 0

    worker_id = f"outbox-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    poll_seconds = max(1, int(os.environ.get("OUTBOX_POLL_SECONDS", "2")))
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _stop)

    while not STOP:
        conn = db.get_db()
        try:
            did_work = process_one(conn, worker_id)
        finally:
            try:
                conn.close()
            except Exception:
                pass
        if not did_work:
            time.sleep(poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
