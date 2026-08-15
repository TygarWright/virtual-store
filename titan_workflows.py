"""Small durable workflow coordinator inspired by mature workflow engines.

The coordinator is intentionally database-backed and synchronous. It records
steps, supports deterministic retries/resumption, and lets callers define
compensation without introducing a heavyweight workflow dependency.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class WorkflowStep:
    name: str
    run: Callable[[Any, dict], Any]
    compensate: Callable[[Any, dict], Any] | None = None


class WorkflowError(RuntimeError):
    pass


class WorkflowWaiting:
    """Signal that a workflow has reached an external wait state."""
    def __init__(self, reason: str = "waiting for external event"):
        self.reason = reason


WAIT = object()


class DurableWorkflow:
    """Execute ordered steps while persisting state after every transition."""

    def __init__(self, conn, *, workflow_type: str, aggregate_type: str, aggregate_id: str | int,
                 workflow_id: str | None = None):
        self.conn = conn
        self.workflow_id = workflow_id or str(uuid.uuid4())
        self.trace_id = None
        self.workflow_type = workflow_type
        self.aggregate_type = aggregate_type
        self.aggregate_id = str(aggregate_id)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS workflow_runs (
                workflow_id TEXT PRIMARY KEY,
                workflow_type TEXT NOT NULL,
                aggregate_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                current_step INTEGER NOT NULL DEFAULT 0,
                context_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TEXT,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                compensation_status TEXT NOT NULL DEFAULT 'not_needed',
                trace_id TEXT
            )"""
        )
        for ddl in (
            "ALTER TABLE workflow_runs ADD COLUMN trace_id TEXT",
            "ALTER TABLE workflow_runs ADD COLUMN last_attempt_at TEXT",
            "ALTER TABLE workflow_runs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3",
        ):
            try:
                self.conn.execute(ddl)
            except Exception as exc:
                if "duplicate column" not in str(exc).lower() and "already exists" not in str(exc).lower():
                    raise
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS workflow_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL REFERENCES workflow_runs(workflow_id) ON DELETE CASCADE,
                step_index INTEGER NOT NULL,
                step_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                result_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '',
                started_at TEXT,
                completed_at TEXT,
                UNIQUE(workflow_id, step_index)
            )"""
        )
        self.conn.commit()

    def _load(self):
        return self.conn.execute("SELECT * FROM workflow_runs WHERE workflow_id=?", (self.workflow_id,)).fetchone()

    def _ensure_run(self, context: dict) -> None:
        existing = self._load()
        if existing:
            return
        now = _now()
        self.conn.execute(
            "INSERT INTO workflow_runs(workflow_id,workflow_type,aggregate_type,aggregate_id,status,current_step,context_json,created_at,updated_at,trace_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (self.workflow_id, self.workflow_type, self.aggregate_type, self.aggregate_id, "running", 0,
             json.dumps(context or {}, sort_keys=True, default=str), now, now, self.trace_id),
        )
        self.conn.commit()

    def status(self) -> dict:
        row = self._load()
        if not row:
            return {"workflow_id": self.workflow_id, "status": "pending", "current_step": 0, "context": {}}
        return {"workflow_id": self.workflow_id, "status": row["status"], "current_step": int(row["current_step"] or 0), "context": json.loads(row["context_json"] or "{}"), "error": row["error"] or "", "compensation_status": row["compensation_status"] or "not_needed"}

    def run(self, steps: Iterable[WorkflowStep], *, context: dict | None = None) -> dict:
        steps = list(steps)
        state = self._load()
        if state and state["status"] == "completed":
            return {"workflow_id": self.workflow_id, "status": "completed", "context": json.loads(state["context_json"] or "{}")}
        ctx = dict(context or {})
        self.trace_id = ctx.get("trace_id") or self.trace_id
        if state:
            ctx.update(json.loads(state["context_json"] or "{}"))
            if state["status"] == "failed" and int(state["attempt_count"] or 0) >= int(state["max_attempts"] or 3):
                raise WorkflowError(f"workflow {self.workflow_type} exhausted max attempts; explicit recovery required")
        self._ensure_run(ctx)
        state = self._load()
        start_index = int(state["current_step"] or 0)
        completed: list[WorkflowStep] = []

        for index in range(start_index, len(steps)):
            step = steps[index]
            now = _now()
            self.conn.execute(
                "INSERT OR IGNORE INTO workflow_steps(workflow_id,step_index,step_name,status) VALUES(?,?,?,'pending')",
                (self.workflow_id, index, step.name),
            )
            self.conn.execute(
                "UPDATE workflow_steps SET status='running',started_at=?,error='' WHERE workflow_id=? AND step_index=?",
                (now, self.workflow_id, index),
            )
            self.conn.execute(
                "UPDATE workflow_runs SET attempt_count=COALESCE(attempt_count,0)+1, last_attempt_at=?, status='running', current_step=?, updated_at=? WHERE workflow_id=?",
                (now, index, now, self.workflow_id),
            )
            self.conn.execute(
                "UPDATE workflow_runs SET status='running',current_step=?,context_json=?,updated_at=?,error='' WHERE workflow_id=?",
                (index, json.dumps(ctx, sort_keys=True, default=str), now, self.workflow_id),
            )
            self.conn.commit()
            span_id = span_started = None
            if self.trace_id:
                try:
                    from observability_service import start_span
                    span_id, span_started = start_span(
                        self.conn, trace_id=self.trace_id, kind="workflow",
                        name=f"{self.workflow_type}:{step.name}",
                        request_id=ctx.get("request_id"),
                        attributes={"workflow_id": self.workflow_id, "step_index": index},
                    )
                except Exception:
                    pass
            try:
                result = step.run(self.conn, ctx)
                if span_id:
                    try:
                        from observability_service import finish_span
                        finish_span(self.conn, span_id, span_started, status="ok")
                    except Exception:
                        pass
                if isinstance(result, WorkflowWaiting):
                    self.conn.execute(
                        "UPDATE workflow_steps SET status='waiting',result_json=?,completed_at=NULL WHERE workflow_id=? AND step_index=?",
                        (json.dumps({"reason": result.reason}, sort_keys=True), self.workflow_id, index),
                    )
                    self.conn.execute(
                        "UPDATE workflow_runs SET status='waiting',current_step=?,context_json=?,updated_at=?,error='' WHERE workflow_id=?",
                        (index, json.dumps(ctx, sort_keys=True, default=str), _now(), self.workflow_id),
                    )
                    self.conn.commit()
                    return {"workflow_id": self.workflow_id, "status": "waiting", "context": ctx, "reason": result.reason}
                if isinstance(result, dict):
                    ctx.update(result)
                self.conn.execute(
                    "UPDATE workflow_steps SET status='completed',result_json=?,completed_at=? WHERE workflow_id=? AND step_index=?",
                    (json.dumps(result or {}, sort_keys=True, default=str), _now(), self.workflow_id, index),
                )
                self.conn.execute(
                    "UPDATE workflow_runs SET current_step=?,context_json=?,updated_at=? WHERE workflow_id=?",
                    (index + 1, json.dumps(ctx, sort_keys=True, default=str), _now(), self.workflow_id),
                )
                self.conn.commit()
                completed.append(step)
            except Exception as exc:
                if span_id:
                    try:
                        from observability_service import finish_span
                        finish_span(self.conn, span_id, span_started, status="error", error=str(exc))
                    except Exception:
                        pass
                self.conn.rollback()
                self.conn.execute(
                    "UPDATE workflow_steps SET status='failed',error=? WHERE workflow_id=? AND step_index=?",
                    (str(exc)[:2000], self.workflow_id, index),
                )
                self.conn.execute(
                    "UPDATE workflow_runs SET status='failed',context_json=?,updated_at=?,error=? WHERE workflow_id=?",
                    (json.dumps(ctx, sort_keys=True, default=str), _now(), str(exc)[:2000], self.workflow_id),
                )
                self.conn.commit()
                compensation_failed = False
                for done in reversed(completed):
                    if done.compensate:
                        try:
                            done.compensate(self.conn, ctx)
                        except Exception:
                            compensation_failed = True
                self.conn.execute(
                    "UPDATE workflow_runs SET compensation_status=?, updated_at=? WHERE workflow_id=?",
                    ("failed" if compensation_failed else ("completed" if completed else "not_needed"), _now(), self.workflow_id),
                )
                self.conn.commit()
                raise WorkflowError(f"workflow {self.workflow_type} failed at {step.name}: {exc}") from exc

        self.conn.execute(
            "UPDATE workflow_runs SET status='completed',current_step=?,updated_at=?,completed_at=?,context_json=? WHERE workflow_id=?",
            (len(steps), _now(), _now(), json.dumps(ctx, sort_keys=True, default=str), self.workflow_id),
        )
        self.conn.commit()
        return {"workflow_id": self.workflow_id, "status": "completed", "context": ctx}


__all__ = ["DurableWorkflow", "WorkflowStep", "WorkflowError", "WorkflowWaiting", "WAIT"]
