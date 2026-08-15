"""Lightweight correlated observability for TITAN.

Stores a bounded history of request/workflow spans in the primary database so
operators can follow a transaction without requiring a distributed tracing
stack. Sensitive request data is deliberately excluded.
"""
from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import os


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS observability_spans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trace_id TEXT NOT NULL,
        span_id TEXT NOT NULL UNIQUE,
        parent_span_id TEXT,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'ok',
        started_at TEXT NOT NULL,
        ended_at TEXT,
        duration_ms REAL,
        request_id TEXT,
        actor_id INTEGER,
        attributes_json TEXT NOT NULL DEFAULT '{}',
        error TEXT NOT NULL DEFAULT ''
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_trace ON observability_spans(trace_id, started_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_recent ON observability_spans(started_at DESC)")
    conn.execute("""CREATE TABLE IF NOT EXISTS observability_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trace_id TEXT NOT NULL DEFAULT '',
        alert_type TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'medium',
        title TEXT NOT NULL,
        details TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL,
        resolved_at TEXT,
        resolved_by INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_alerts_status ON observability_alerts(status, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_alerts_trace ON observability_alerts(trace_id, created_at DESC)")
    conn.execute("""CREATE TABLE IF NOT EXISTS observability_slo_policies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        operation_pattern TEXT NOT NULL,
        target_percent REAL NOT NULL DEFAULT 99.0,
        window_hours INTEGER NOT NULL DEFAULT 24,
        max_latency_ms REAL,
        enabled INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL
    )""")
    conn.commit()


def ensure_alert_policy_schema(conn) -> None:
    ensure_schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS observability_alert_policies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_type TEXT NOT NULL UNIQUE,
        enabled INTEGER NOT NULL DEFAULT 1,
        severity TEXT NOT NULL DEFAULT 'medium',
        threshold_ms INTEGER,
        cooldown_minutes INTEGER NOT NULL DEFAULT 10,
        notify_admins INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL
    )""")
    conn.commit()

def alert_policies(conn):
    ensure_alert_policy_schema(conn)
    return conn.execute("SELECT * FROM observability_alert_policies ORDER BY alert_type").fetchall()

def set_alert_policy(conn, *, alert_type: str, enabled: bool, severity: str, threshold_ms: int | None = None, cooldown_minutes: int = 10, notify_admins: bool = True):
    ensure_alert_policy_schema(conn)
    now=now_iso()
    conn.execute("""INSERT INTO observability_alert_policies(alert_type,enabled,severity,threshold_ms,cooldown_minutes,notify_admins,updated_at) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(alert_type) DO UPDATE SET enabled=excluded.enabled,severity=excluded.severity,threshold_ms=excluded.threshold_ms,cooldown_minutes=excluded.cooldown_minutes,notify_admins=excluded.notify_admins,updated_at=excluded.updated_at""", (alert_type,1 if enabled else 0,severity,threshold_ms,int(cooldown_minutes),1 if notify_admins else 0,now))
    conn.commit()

def start_span(conn, *, trace_id: str, kind: str, name: str, parent_span_id: str | None = None,
               request_id: str | None = None, actor_id: int | None = None, attributes: dict | None = None):
    ensure_schema(conn)
    span_id = uuid.uuid4().hex[:16]
    started_at = now_iso()
    conn.execute(
        "INSERT INTO observability_spans(trace_id,span_id,parent_span_id,kind,name,status,started_at,request_id,actor_id,attributes_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (trace_id, span_id, parent_span_id, kind, name, "running", started_at, request_id, actor_id,
         json.dumps(attributes or {}, sort_keys=True, default=str)),
    )
    conn.commit()
    return span_id, time.perf_counter()


def finish_span(conn, span_id: str, started_perf: float, *, status: str = "ok", error: str = "") -> None:
    duration_ms = round((time.perf_counter() - started_perf) * 1000, 3)
    conn.execute(
        "UPDATE observability_spans SET status=?, ended_at=?, duration_ms=?, error=? WHERE span_id=?",
        (status, now_iso(), duration_ms, str(error)[:2000], span_id),
    )
    # Keep the table bounded without a separate cron job.
    conn.execute("DELETE FROM observability_spans WHERE id NOT IN (SELECT id FROM observability_spans ORDER BY id DESC LIMIT 5000)")
    conn.commit()


@contextmanager
def span(conn, *, trace_id: str, kind: str, name: str, parent_span_id: str | None = None,
         request_id: str | None = None, actor_id: int | None = None, attributes: dict | None = None):
    span_id, started = start_span(
        conn, trace_id=trace_id, kind=kind, name=name, parent_span_id=parent_span_id,
        request_id=request_id, actor_id=actor_id, attributes=attributes,
    )
    try:
        yield span_id
    except Exception as exc:
        finish_span(conn, span_id, started, status="error", error=str(exc))
        raise
    else:
        finish_span(conn, span_id, started)


def recent_spans(conn, *, trace_id: str | None = None, operation: str | None = None, limit: int = 200):
    ensure_schema(conn)
    limit = max(1, min(int(limit), 500))
    if trace_id:
        if operation:
            return conn.execute(
                "SELECT * FROM observability_spans WHERE trace_id=? AND name LIKE ? ORDER BY started_at ASC LIMIT ?",
                (trace_id, f"%{operation}%", limit),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM observability_spans WHERE trace_id=? ORDER BY started_at ASC LIMIT ?",
            (trace_id, limit),
        ).fetchall()
    if operation:
        return conn.execute(
            "SELECT * FROM observability_spans WHERE name LIKE ? ORDER BY started_at DESC LIMIT ?",
            (f"%{operation}%", limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM observability_spans ORDER BY started_at DESC LIMIT ?",
        (limit,),
    ).fetchall()


__all__ = ["ensure_schema", "start_span", "finish_span", "span", "recent_spans"]


def trace_summary(conn, trace_id: str):
    rows = recent_spans(conn, trace_id=trace_id, limit=500)
    if not rows:
        return {"trace_id": trace_id, "spans": [], "duration_ms": 0, "errors": 0, "root": None}
    data=[dict(r) for r in rows]
    start=min(r["started_at"] for r in data)
    end=max((r.get("ended_at") or r["started_at"]) for r in data)
    try:
        duration=max((datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds()*1000,0)
    except Exception:
        duration=sum(float(r.get("duration_ms") or 0) for r in data)
    errors=sum(1 for r in data if str(r.get("status"))=="error")
    slowest=max(data, key=lambda r: float(r.get("duration_ms") or 0), default=None)
    by_kind={}
    for r in data:
        by_kind[r["kind"]]=by_kind.get(r["kind"], 0)+1
    by_parent={}
    for r in data:
        by_parent.setdefault(r.get("parent_span_id"), []).append(r)
    def node(row):
        return {"span_id": row["span_id"], "parent_span_id": row.get("parent_span_id"), "kind": row["kind"], "name": row["name"], "status": row["status"], "duration_ms": row.get("duration_ms"), "started_at": row["started_at"], "ended_at": row.get("ended_at"), "error": row.get("error") or "", "children":[node(c) for c in by_parent.get(row["span_id"], [])]}
    roots=[node(r) for r in data if not r.get("parent_span_id") or r.get("parent_span_id") not in {x["span_id"] for x in data}]
    return {"trace_id": trace_id, "spans": data, "duration_ms": round(duration,3), "errors": errors, "root": roots[0] if roots else None, "slowest": slowest, "by_kind": by_kind}



def emit_alert(conn, *, trace_id: str, alert_type: str, severity: str, title: str, details: str, notify: bool = True) -> int | None:
    """Create a deduplicated operational alert and optionally notify active admins."""
    ensure_alert_policy_schema(conn)
    policy = conn.execute("SELECT enabled, severity, cooldown_minutes, notify_admins FROM observability_alert_policies WHERE alert_type=?", (alert_type,)).fetchone()
    if policy and not int(policy[0]):
        return None
    if policy:
        severity = str(policy[1] or severity)
        notify = bool(int(policy[3]))
    cooldown = int(policy[2]) if policy else 10
    now = now_iso()
    try:
        duplicate = conn.execute(
            "SELECT id FROM observability_alerts WHERE alert_type=? AND title=? AND status='open' AND created_at > ? LIMIT 1",
            (alert_type, title, (datetime.now(timezone.utc) - timedelta(minutes=cooldown)).isoformat()),
        ).fetchone()
    except Exception:
        duplicate = None
    if duplicate:
        return int(duplicate["id"])
    cur = conn.execute(
        "INSERT INTO observability_alerts(trace_id,alert_type,severity,title,details,created_at) VALUES(?,?,?,?,?,?)",
        (trace_id or "", alert_type, severity, title, details, now),
    )
    alert_id = int(cur.lastrowid)
    if notify and severity in {"high", "critical"}:
        try:
            admins = conn.execute("SELECT id FROM admin_users WHERE is_active=1").fetchall()
            for admin in admins:
                conn.execute(
                    "INSERT INTO team_notifications(admin_id,kind,title,body,created_at) VALUES(?,?,?,?,?)",
                    (int(admin["id"]), "observability_alert", f"Operational alert: {title}", f"{severity.upper()}: {details[:700]}", now),
                )
        except Exception:
            pass
    conn.commit()
    return alert_id

def recent_alerts(conn, *, status: str = "open", limit: int = 100):
    ensure_schema(conn)
    try:
        rows=conn.execute("SELECT * FROM observability_alerts WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, max(1,min(int(limit),200)))).fetchall()
    except Exception:
        return []
    return rows


def resolve_alert(conn, alert_id: int, *, admin_id: int) -> bool:
    try:
        row=conn.execute("SELECT id,status FROM observability_alerts WHERE id=?", (int(alert_id),)).fetchone()
        if not row or row["status"]=="resolved":
            return False
        conn.execute("UPDATE observability_alerts SET status='resolved', resolved_at=?, resolved_by=? WHERE id=?", (now_iso(), int(admin_id), int(alert_id)))
        conn.commit(); return True
    except Exception:
        return False

__all__ += ["trace_summary", "emit_alert", "recent_alerts", "resolve_alert"]

# Round 50: operational SLOs and release correlation.
def ensure_slo_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS observability_slo_policies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        operation_pattern TEXT NOT NULL,
        target_percent REAL NOT NULL DEFAULT 99.0,
        window_hours INTEGER NOT NULL DEFAULT 24,
        max_latency_ms REAL,
        enabled INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL
    )""")
    conn.commit()

def slo_policies(conn):
    ensure_slo_schema(conn)
    return conn.execute("SELECT * FROM observability_slo_policies ORDER BY name").fetchall()

def upsert_slo_policy(conn, *, key, name, operation_pattern, target_percent=99.0, window_hours=24, max_latency_ms=None, enabled=True):
    ensure_slo_schema(conn)
    conn.execute("""INSERT INTO observability_slo_policies(key,name,operation_pattern,target_percent,window_hours,max_latency_ms,enabled,updated_at)
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(key) DO UPDATE SET name=excluded.name,operation_pattern=excluded.operation_pattern,target_percent=excluded.target_percent,
        window_hours=excluded.window_hours,max_latency_ms=excluded.max_latency_ms,enabled=excluded.enabled,updated_at=excluded.updated_at""",
        (key, name, operation_pattern, float(target_percent), int(window_hours), max_latency_ms, 1 if enabled else 0, now_iso()))
    conn.commit()

def slo_report(conn, *, key):
    ensure_slo_schema(conn)
    policy = conn.execute("SELECT * FROM observability_slo_policies WHERE key=?", (key,)).fetchone()
    if not policy:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=int(policy[5]))
    rows = conn.execute("SELECT status,duration_ms FROM observability_spans WHERE name LIKE ? AND started_at>=?", (f"%{policy[3]}%", cutoff.isoformat())).fetchall()
    total = len(rows)
    errors = sum(1 for r in rows if str(r['status']) == 'error')
    good = total - errors
    availability = (good / total * 100.0) if total else 100.0
    latencies = sorted(float(r['duration_ms'] or 0) for r in rows)
    p95 = latencies[min(len(latencies)-1, max(0, int(len(latencies)*0.95)-1))] if latencies else 0.0
    latency_ok = True if policy[6] is None else p95 <= float(policy[6])
    target_ok = availability >= float(policy[4])
    return {
        'key': policy['key'], 'name': policy['name'], 'target_percent': float(policy['target_percent']),
        'window_hours': int(policy['window_hours']), 'max_latency_ms': policy['max_latency_ms'],
        'total': total, 'errors': errors, 'availability_percent': round(availability, 3),
        'p95_ms': round(p95, 3), 'availability_ok': target_ok, 'latency_ok': latency_ok,
        'healthy': bool(target_ok and latency_ok),
        'release': os.environ.get('RENDER_GIT_COMMIT') or os.environ.get('GIT_COMMIT') or os.environ.get('APP_VERSION') or 'unknown',
    }

__all__ += ['ensure_slo_schema','slo_policies','upsert_slo_policy','slo_report']
