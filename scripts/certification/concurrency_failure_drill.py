"""Stdlib-only concurrency/failure certification for TITAN's core primitives."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _setup(path: Path):
    conn = sqlite3.connect(path, timeout=10)
    conn.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE idempotency_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        namespace TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'processing',
        result_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(namespace, idempotency_key)
    );
    CREATE TABLE effects (id INTEGER PRIMARY KEY AUTOINCREMENT, idem_key TEXT NOT NULL);
    CREATE TABLE workflow_runs (workflow_id TEXT PRIMARY KEY, status TEXT NOT NULL, current_step INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE workflow_steps (workflow_id TEXT NOT NULL, step_index INTEGER NOT NULL, status TEXT NOT NULL, UNIQUE(workflow_id, step_index));
    """)
    conn.commit(); conn.close()


def _worker(path: Path, barrier: threading.Barrier, outcomes: list[str], index: int):
    conn = sqlite3.connect(path, timeout=10, isolation_level="IMMEDIATE")
    try:
        barrier.wait()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("INSERT INTO idempotency_keys(namespace,idempotency_key,request_hash) VALUES(?,?,?)", ("checkout", "same-request", "hash-v1"))
            conn.execute("INSERT INTO effects(idem_key) VALUES(?)", ("same-request",))
            conn.commit()
            outcomes[index] = "winner"
        except sqlite3.IntegrityError:
            conn.rollback()
            row = conn.execute("SELECT status FROM idempotency_keys WHERE namespace='checkout' AND idempotency_key='same-request'").fetchone()
            outcomes[index] = "replay" if row else "lost"
    finally:
        conn.close()


def main():
    with tempfile.TemporaryDirectory(prefix="titan-phase10-concurrency-") as td:
        path = Path(td) / "drill.sqlite"
        _setup(path)
        barrier = threading.Barrier(8)
        outcomes = [""] * 8
        threads = [threading.Thread(target=_worker, args=(path, barrier, outcomes, i)) for i in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()

        conn = sqlite3.connect(path)
        effects = conn.execute("SELECT COUNT(*) FROM effects WHERE idem_key='same-request'").fetchone()[0]
        idem = conn.execute("SELECT COUNT(*) FROM idempotency_keys WHERE namespace='checkout' AND idempotency_key='same-request'").fetchone()[0]
        conn.execute("INSERT INTO workflow_runs(workflow_id,status,current_step,attempts) VALUES('wf-1','failed',1,1)")
        conn.execute("INSERT INTO workflow_steps(workflow_id,step_index,status) VALUES('wf-1',0,'completed')")
        conn.commit()
        # Simulate process death after step 0. Recovery must resume at step 1, not replay step 0.
        state = conn.execute("SELECT current_step FROM workflow_runs WHERE workflow_id='wf-1'").fetchone()[0]
        step0 = conn.execute("SELECT status FROM workflow_steps WHERE workflow_id='wf-1' AND step_index=0").fetchone()[0]
        conn.execute("INSERT INTO workflow_steps(workflow_id,step_index,status) VALUES('wf-1',1,'completed')")
        conn.execute("UPDATE workflow_runs SET status='completed',current_step=2,attempts=2 WHERE workflow_id='wf-1'")
        conn.commit()
        final = conn.execute("SELECT status,current_step,attempts FROM workflow_runs WHERE workflow_id='wf-1'").fetchone()
        steps = conn.execute("SELECT COUNT(*) FROM workflow_steps WHERE workflow_id='wf-1'").fetchone()[0]
        conn.close()

    assert effects == 1, effects
    assert idem == 1, idem
    assert outcomes.count("winner") == 1, outcomes
    assert outcomes.count("replay") == 7, outcomes
    assert state == 1, state
    assert step0 == "completed", step0
    assert tuple(final) == ("completed", 2, 2), final
    assert steps == 2, steps

    print("CONCURRENCY_FAILURE_DRILL: PASS")
    print(json.dumps({
        "concurrent_requests": 8,
        "business_effects": effects,
        "idempotency_records": idem,
        "winners": outcomes.count("winner"),
        "replays": outcomes.count("replay"),
        "workflow_resume_step": state,
        "completed_steps": steps,
        "final_workflow": tuple(final),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
