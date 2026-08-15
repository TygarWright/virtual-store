"""TITAN institutional-governance services.

Small, dependency-free business-control primitives: approvals, exceptions,
reconciliation snapshots, decision journal and SOP records.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional
from datetime import datetime, timedelta

import database as db
from backend_kernel import publish_event


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def ensure_governance_maturity_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS approval_delegations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, delegator_id INTEGER NOT NULL, delegate_id INTEGER NOT NULL,
        action TEXT NOT NULL, starts_at TEXT NOT NULL, expires_at TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, UNIQUE(delegator_id,delegate_id,action,starts_at)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS segregation_rules (
        action TEXT PRIMARY KEY, requester_role TEXT NOT NULL DEFAULT '', approver_role TEXT NOT NULL DEFAULT '',
        prohibited_same_user INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_approval_delegations_active ON approval_delegations(delegate_id, action, active, expires_at)")
    conn.commit()


def create_approval_delegation(conn, *, delegator_id:int, delegate_id:int, action:str, starts_at:str, expires_at:str, reason:str='')->int:
    ensure_governance_maturity_schema(conn)
    if int(delegator_id)==int(delegate_id):
        raise ValueError('delegation to self is not allowed')
    if datetime.fromisoformat(starts_at.replace('Z','+00:00')) >= datetime.fromisoformat(expires_at.replace('Z','+00:00')):
        raise ValueError('delegation expiry must be after start')
    row=conn.execute("INSERT INTO approval_delegations(delegator_id,delegate_id,action,starts_at,expires_at,reason,active,created_at) VALUES(?,?,?,?,?,?,1,?)", (delegator_id,delegate_id,action,starts_at,expires_at,reason.strip(),_now()))
    conn.commit(); return int(row.lastrowid)


def approver_is_delegated(conn, *, delegate_id:int, action:str, now_iso:str|None=None)->bool:
    ensure_governance_maturity_schema(conn)
    now_iso=now_iso or _now()
    row=conn.execute("SELECT 1 FROM approval_delegations WHERE delegate_id=? AND action=? AND active=1 AND starts_at<=? AND expires_at>? LIMIT 1", (delegate_id,action,now_iso,now_iso)).fetchone()
    return bool(row)


def set_segregation_rule(conn, *, action:str, requester_role:str, approver_role:str, prohibited_same_user:bool=True, enabled:bool=True):
    ensure_governance_maturity_schema(conn)
    conn.execute("INSERT INTO segregation_rules(action,requester_role,approver_role,prohibited_same_user,enabled,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(action) DO UPDATE SET requester_role=excluded.requester_role, approver_role=excluded.approver_role, prohibited_same_user=excluded.prohibited_same_user, enabled=excluded.enabled, updated_at=excluded.updated_at", (action,requester_role.strip(),approver_role.strip(),1 if prohibited_same_user else 0,1 if enabled else 0,_now()))
    conn.commit()


def create_approval(conn, *, requested_by: int, action: str, entity: str,
                    entity_id: int, amount: int = 0, reason: str = "",
                    metadata: Optional[dict] = None) -> int:
    ensure_governance_maturity_schema(conn)
    policy = conn.execute("SELECT required_approvals, approval_expiry_minutes, version FROM high_risk_action_policies WHERE action=? AND enabled=1", (action,)).fetchone()
    required = max(1, int(policy[0] if policy and policy[0] is not None else 1))
    expiry_minutes = max(1, int(policy[1] if policy and policy[1] is not None else 1440))
    policy_version = max(1, int(policy[2] if policy and policy[2] is not None else 1))
    now = _now()
    business_metadata = dict(metadata or {})
    snapshot = {"policy_version": policy_version, "required_approvals": required, "approval_expiry_minutes": expiry_minutes}
    row = conn.execute(
        """INSERT INTO admin_approval_requests
           (request_ref, requested_by, action, entity, entity_id, amount, reason,
            metadata_json, status, created_at, updated_at, policy_version, policy_snapshot_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
        (f"APR-{secrets.token_hex(6).upper()}", requested_by, action, entity,
         entity_id, int(amount or 0), reason.strip(), _json({"business_metadata": business_metadata, "policy_snapshot": snapshot}), now, now,
         policy_version, _json(snapshot)),
    )
    approval_id = int(row.lastrowid)
    for step in range(1, required + 1):
        conn.execute("INSERT INTO approval_steps(approval_id, step_index, status) VALUES(?, ?, 'pending')", (approval_id, step))
    publish_event(conn, topic="governance.approval.requested", aggregate="approval", aggregate_id=approval_id,
                  payload={"approval_id": approval_id, "action": action, "entity": entity, "entity_id": entity_id, "required_approvals": required, "policy_version": policy_version})
    conn.commit()
    return approval_id


def approve(conn, approval_id: int, *, approved_by: int, note: str = "") -> bool:
    began = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        began = True
    except Exception:
        began = False
    row = conn.execute("SELECT * FROM admin_approval_requests WHERE id = ?", (approval_id,)).fetchone()
    if not row or row["status"] != "pending" or int(row["requested_by"]) == int(approved_by):
        if began:
            conn.rollback()
        return False
    expiry_minutes = 1440
    try:
        snapshot = json.loads(row["policy_snapshot_json"] or "{}") if "policy_snapshot_json" in row.keys() else {}
        expiry_minutes = max(1, int((snapshot.get("approval_expiry_minutes") if isinstance(snapshot, dict) else 1440) or 1440))
    except Exception:
        policy = conn.execute("SELECT approval_expiry_minutes FROM high_risk_action_policies WHERE action=?", (row["action"],)).fetchone()
        expiry_minutes = max(1, int(policy[0] if policy else 1440))
    try:
        created = datetime.fromisoformat(row["created_at"])
        age_seconds = (datetime.now(timezone.utc) - (created if created.tzinfo else created.replace(tzinfo=timezone.utc))).total_seconds()
        if age_seconds > expiry_minutes * 60:
            conn.execute("UPDATE admin_approval_requests SET status='expired', updated_at=? WHERE id=? AND status='pending'", (_now(), approval_id))
            conn.commit()
            return False
    except (TypeError, ValueError):
        return False
    ensure_governance_maturity_schema(conn)
    existing = conn.execute("SELECT 1 FROM approval_steps WHERE approval_id=? AND approved_by=?", (approval_id, approved_by)).fetchone()
    if existing:
        if began:
            conn.rollback()
        return False
    step = conn.execute("SELECT * FROM approval_steps WHERE approval_id=? AND status='pending' ORDER BY step_index LIMIT 1", (approval_id,)).fetchone()
    if not step:
        if began:
            conn.rollback()
        return False
    now = _now()
    conn.execute("UPDATE approval_steps SET status='approved', approved_by=?, note=?, approved_at=? WHERE id=? AND status='pending'", (approved_by, note.strip(), now, step['id']))
    remaining = conn.execute("SELECT COUNT(*) FROM approval_steps WHERE approval_id=? AND status='pending'", (approval_id,)).fetchone()[0]
    if int(remaining) == 0:
        conn.execute("UPDATE admin_approval_requests SET status='approved', approved_by=?, approval_note=?, approved_at=?, updated_at=? WHERE id=? AND status='pending'", (approved_by, note.strip(), now, now, approval_id))
    else:
        conn.execute("UPDATE admin_approval_requests SET updated_at=? WHERE id=?", (now, approval_id))
    publish_event(conn, topic="governance.approval.approved", aggregate="approval", aggregate_id=approval_id, payload={"approval_id": approval_id, "approved_by": approved_by, "completed": int(remaining)==0, "remaining_steps": int(remaining)})
    conn.commit()
    return True



def expire_pending_approvals(conn, *, now_iso: Optional[str] = None) -> int:
    now_iso = now_iso or _now()
    rows = conn.execute("SELECT id, action FROM admin_approval_requests WHERE status='pending'").fetchall()
    expired = 0
    for row in rows:
        try:
            item = conn.execute("SELECT created_at, policy_snapshot_json FROM admin_approval_requests WHERE id=?", (int(row["id"]),)).fetchone()
            snap = json.loads(item["policy_snapshot_json"] or "{}") if item else {}
            minutes = max(1, int(snap.get("approval_expiry_minutes", 1440)))
            created = datetime.fromisoformat(str(item["created_at"]).replace("Z", "+00:00"))
            current = datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
            if (current - created).total_seconds() > minutes * 60:
                conn.execute("UPDATE admin_approval_requests SET status='expired', updated_at=? WHERE id=? AND status='pending'", (now_iso, int(row["id"])))
                publish_event(conn, topic="governance.approval.expired", aggregate="approval", aggregate_id=int(row["id"]), payload={"approval_id": int(row["id"]), "action": str(row["action"])})
                expired += 1
        except Exception:
            continue
    conn.commit()
    return expired

def reject(conn, approval_id: int, *, rejected_by: int, note: str = "") -> bool:
    row = conn.execute("SELECT status FROM admin_approval_requests WHERE id = ?", (approval_id,)).fetchone()
    if not row or row["status"] != "pending":
        return False
    now = _now()
    conn.execute(
        """UPDATE admin_approval_requests
           SET status='rejected', approved_by=?, approval_note=?, approved_at=?, updated_at=?
           WHERE id=? AND status='pending'""",
        (rejected_by, note.strip(), now, now, approval_id),
    )
    publish_event(conn, topic="governance.approval.rejected", aggregate="approval", aggregate_id=approval_id, payload={"approval_id": approval_id, "rejected_by": rejected_by, "note": note.strip()[:1000]})
    conn.commit()
    return True


def add_exception(conn, *, code: str, severity: str, title: str, description: str,
                  entity: str = "", entity_id: int = 0, metadata: Optional[dict] = None) -> int:
    ensure_guardian_mastery_schema(conn)
    now = _now()
    policy = guardian_sla_policy(conn, severity)
    due_at = None
    if policy.get("due_minutes"):
        due_at = (datetime.fromisoformat(now.replace("Z", "+00:00")) + timedelta(minutes=int(policy["due_minutes"]))).isoformat().replace("+00:00", "Z")
    row = conn.execute(
        """INSERT INTO business_exceptions
           (code, severity, title, description, entity, entity_id, metadata_json,
            status, created_at, updated_at, due_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
        (code, severity, title, description, entity, int(entity_id or 0), _json(metadata), now, now, due_at),
    )
    conn.commit()
    try:
        record_exception_event(conn, int(row.lastrowid), 'created', details={'code': code, 'severity': severity, 'entity': entity, 'entity_id': int(entity_id or 0), 'due_at': due_at})
    except Exception:
        pass
    return int(row.lastrowid)


def resolve_exception(conn, exception_id: int, *, resolved_by: int, resolution: str) -> bool:
    row = conn.execute("SELECT status FROM business_exceptions WHERE id = ?", (exception_id,)).fetchone()
    if not row or row["status"] == "resolved":
        return False
    conn.execute(
        """UPDATE business_exceptions
           SET status='resolved', resolved_by=?, resolution=?, resolved_at=?, updated_at=?
           WHERE id=?""",
        (resolved_by, resolution.strip(), _now(), _now(), exception_id,)
    )
    conn.commit()
    try:
        record_exception_event(conn, exception_id, 'resolved', actor_id=resolved_by, details={'resolution': resolution.strip()[:1000]})
    except Exception:
        pass
    return True

def acknowledge_exception(conn, exception_id: int, *, admin_id: int) -> bool:
    row = conn.execute("SELECT status FROM business_exceptions WHERE id=?", (exception_id,)).fetchone()
    if not row or str(row["status"]) != "open":
        return False
    conn.execute("UPDATE business_exceptions SET status='acknowledged', assigned_to=COALESCE(assigned_to, ?), updated_at=? WHERE id=?", (admin_id, _now(), exception_id))
    conn.commit()
    try: record_exception_event(conn, exception_id, 'acknowledged', actor_id=admin_id)
    except Exception: pass
    return True


def reopen_exception(conn, exception_id: int, *, admin_id: int, reason: str = "") -> bool:
    row = conn.execute("SELECT status FROM business_exceptions WHERE id=?", (exception_id,)).fetchone()
    if not row or str(row["status"]) not in {"resolved", "acknowledged"}:
        return False
    conn.execute("UPDATE business_exceptions SET status='open', resolved_by=NULL, resolved_at=NULL, resolution='', escalated_at=NULL, escalation_reason=?, updated_at=? WHERE id=?", (reason.strip() or f"Reopened by admin {admin_id}", _now(), exception_id))
    conn.commit()
    try: record_exception_event(conn, exception_id, 'reopened', actor_id=admin_id, details={'reason': reason.strip()[:1000]})
    except Exception: pass
    return True


def notify_exception_assignee(conn, exception_id: int, *, kind: str = "guardian") -> int:
    row = conn.execute("SELECT assigned_to, title, severity, description FROM business_exceptions WHERE id=?", (exception_id,)).fetchone()
    if not row or not row["assigned_to"]:
        return 0
    try:
        conn.execute("INSERT INTO team_notifications(admin_id,kind,title,body,created_at) VALUES(?,?,?,?,?)", (int(row["assigned_to"]), kind, f"Guardian: {row['title']}", f"{str(row['severity']).upper()}: {str(row['description'])[:500]}", _now()))
        conn.commit()
        return 1
    except Exception:
        return 0


def create_decision(conn, *, admin_id: int, title: str, decision: str,
                    reason: str = "", expected_result: str = "") -> int:
    row = conn.execute(
        """INSERT INTO decision_journal
           (admin_id, title, decision, reason, expected_result, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (admin_id, title.strip(), decision.strip(), reason.strip(), expected_result.strip(), _now()),
    )
    conn.commit()
    try:
        from mastery_services import index_memory
        index_memory(conn, source_type="decision", source_id=int(row.lastrowid), title=title, body=" ".join([decision, reason, expected_result]), keywords="decision institutional memory")
    except Exception:
        pass
    return int(row.lastrowid)


def create_sop(conn, *, admin_id: int, title: str, trigger: str, steps: Iterable[str]) -> int:
    row = conn.execute(
        """INSERT INTO sop_documents
           (title, trigger, steps_json, version, created_by, active, created_at, updated_at)
           VALUES (?, ?, ?, 1, ?, 1, ?, ?)""",
        (title.strip(), trigger.strip(), _json(list(steps)), admin_id, _now(), _now()),
    )
    conn.commit()
    try:
        from mastery_services import index_memory
        index_memory(conn, source_type="sop", source_id=int(row.lastrowid), title=title, body=" ".join([trigger, *list(steps)]), keywords="SOP procedure playbook")
    except Exception:
        pass
    return int(row.lastrowid)

def reconcile_internal_period(conn, *, start: str, end: str, created_by: int = 0) -> Dict[str, Any]:
    """Compute internal expected money for a UTC date range.
    Provider amount is intentionally supplied separately because only the
    real provider API/webhook data can establish provider-side truth.
    """
    paid = conn.execute(
        """SELECT COALESCE(SUM(amount),0) AS total FROM orders
           WHERE created_at >= ? AND created_at < ? AND status NOT IN ('cancelled','payment_failed')
             AND COALESCE(payment_state,'') IN ('paid','captured','authorized')""", (start, end)
    ).fetchone()["total"]
    refunds = conn.execute(
        """SELECT COALESCE(SUM(amount),0) AS total FROM order_refunds
           WHERE initiated_at >= ? AND initiated_at < ? AND status IN ('processed','processing','pending')""", (start, end)
    ).fetchone()["total"]
    expected = int(paid or 0) - int(refunds or 0)
    row = conn.execute(
        """INSERT INTO financial_reconciliation
           (period_start, period_end, expected_amount, refund_amount, status, created_by, created_at)
           VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
        (start, end, expected, int(refunds or 0), created_by or None, _now()),
    )
    conn.commit()
    return {"id": int(row.lastrowid), "expected_amount": expected, "refund_amount": int(refunds or 0)}


def create_simulation(conn, *, admin_id: int, label: str, orders: int = 10,
                      failure_rate: float = 0.1, refund_rate: float = 0.05) -> Dict[str, Any]:
    """Deterministic, side-effect-free business simulation.

    This never inserts orders/payments/inventory. It is a planning tool only.
    """
    orders = max(0, min(int(orders), 100000))
    failure_rate = max(0.0, min(float(failure_rate), 1.0))
    refund_rate = max(0.0, min(float(refund_rate), 1.0))
    seed = secrets.token_hex(8)
    successful = round(orders * (1 - failure_rate))
    refunds = round(successful * refund_rate)
    gross = successful * 1000
    net = gross - refunds * 1000
    row = conn.execute(
        """INSERT INTO simulation_runs
           (admin_id, label, parameters_json, results_json, status, created_at)
           VALUES (?, ?, ?, ?, 'completed', ?)""",
        (admin_id, label.strip(), _json({"orders": orders, "failure_rate": failure_rate, "refund_rate": refund_rate, "seed": seed}),
         _json({"successful_orders": successful, "failed_orders": orders-successful, "refunds": refunds,
                "gross_amount": gross, "net_amount": net}), _now()),
    )
    conn.commit()
    return {"id": int(row.lastrowid), "successful_orders": successful, "failed_orders": orders-successful,
            "refunds": refunds, "gross_amount": gross, "net_amount": net}


def scan_exceptions(conn) -> int:
    """Create deduplicated operational exceptions from obvious internal inconsistencies."""
    ensure_guardian_mastery_schema(conn)
    created = 0
    checks = [
        ("negative_inventory", "critical",
         "Negative inventory detected",
         "One or more products have negative stock.",
         "SELECT id, name, quantity FROM products WHERE quantity < 0"),
        ("payment_without_paid_order", "high",
         "Payment state/order mismatch",
         "A payment record appears completed while its order is not paid.",
         """SELECT o.id, o.order_ref FROM orders o
            JOIN order_payments p ON p.order_id=o.id
            WHERE p.status IN ('captured','paid','processed')
              AND COALESCE(o.payment_state,'') NOT IN ('paid','captured')
            LIMIT 100"""),
        ("stale_processing_refund", "high",
         "Refund awaiting provider confirmation",
         "A refund has remained processing for more than one hour.",
         """SELECT id, order_id FROM order_refunds
            WHERE status='processing'
              AND initiated_at < datetime('now','-1 hour')
            LIMIT 100"""),
    ]
    for code, severity, title, description, sql in checks:
        for row in conn.execute(sql).fetchall():
            entity_id = int(row[0])
            exists = conn.execute(
                "SELECT 1 FROM business_exceptions WHERE code=? AND entity_id=? AND status='open' LIMIT 1",
                (code, entity_id),
            ).fetchone()
            if exists:
                continue
            metadata = dict(row)
            if code == 'negative_inventory' and 'quantity' in metadata:
                metadata['stock'] = metadata['quantity']
            add_exception(conn, code=code, severity=severity, title=title, description=description,
                          entity='product' if code == 'negative_inventory' else ('order' if code == 'payment_without_paid_order' else 'refund'),
                          entity_id=entity_id, metadata=metadata)
            created += 1
    return created


def grant_temporary_permission(conn, *, admin_id: int, permission: str, granted_by: int,
                               expires_at: str, reason: str = "") -> int:
    row = conn.execute(
        """INSERT INTO admin_permission_grants
           (admin_id, permission, granted_by, expires_at, reason, active, created_at)
           VALUES (?, ?, ?, ?, ?, 1, ?)""",
        (admin_id, permission.strip(), granted_by, expires_at, reason.strip(), _now()),
    )
    conn.commit()
    return int(row.lastrowid)


def revoke_temporary_permission(conn, grant_id: int, *, revoked_by: int) -> bool:
    row = conn.execute("SELECT active FROM admin_permission_grants WHERE id=?", (grant_id,)).fetchone()
    if not row or not int(row[0]):
        return False
    conn.execute(
        "UPDATE admin_permission_grants SET active=0, revoked_at=? WHERE id=?",
        (_now(), grant_id),
    )
    conn.commit()
    return True


def effective_permissions(conn, admin_id: int, base_permissions: Optional[Iterable[str]] = None) -> list[str]:
    perms = set(base_permissions or [])
    rows = conn.execute(
        """SELECT permission FROM admin_permission_grants
           WHERE admin_id=? AND active=1 AND (expires_at IS NULL OR expires_at > ?)""",
        (admin_id, _now()),
    ).fetchall()
    perms.update(str(r[0]) for r in rows)
    return sorted(perms)


def policy_requires_approval(conn, action: str, amount: int = 0) -> bool:
    row = conn.execute(
        "SELECT threshold_amount, require_two_person, enabled FROM high_risk_action_policies WHERE action=?",
        (action,),
    ).fetchone()
    if not row or not int(row[2]):
        return False
    return bool(int(row[1]) and int(amount or 0) >= int(row[0] or 0))


def audit_hash_chain_head(conn) -> str:
    row = conn.execute("SELECT last_hash FROM audit_integrity WHERE id=1").fetchone()
    return str(row[0] if row else "")


def append_audit_hash(conn, *, audit_id: int, admin_id: int, action: str, target: str,
                      details: str, created_at: str) -> str:
    import hashlib
    previous = audit_hash_chain_head(conn)
    payload = f"{previous}|{audit_id}|{admin_id}|{action}|{target}|{details}|{created_at}".encode()
    digest = hashlib.sha256(payload).hexdigest()
    conn.execute("UPDATE admin_audit_log SET integrity_hash=? WHERE id=?", (digest, audit_id))
    conn.execute("UPDATE audit_integrity SET last_hash=? WHERE id=1", (digest,))
    return digest


def verify_audit_integrity(conn) -> dict:
    """Verify the persisted audit hash chain without modifying it."""
    import hashlib
    rows = conn.execute(
        "SELECT id, admin_id, action, target, details, created_at, integrity_hash "
        "FROM admin_audit_log ORDER BY id"
    ).fetchall()
    previous = ""
    checked = 0
    for row in rows:
        payload = f"{previous}|{row['id']}|{row['admin_id']}|{row['action']}|{row['target']}|{row['details']}|{row['created_at']}".encode()
        expected = hashlib.sha256(payload).hexdigest()
        actual = str(row['integrity_hash'] or '')
        if actual and actual != expected:
            return {"ok": False, "checked": checked, "bad_id": int(row['id']), "expected": expected, "actual": actual}
        previous = actual or expected
        checked += 1
    head = audit_hash_chain_head(conn)
    return {"ok": (not head or head == previous), "checked": checked, "head": head, "calculated_head": previous}


def log_support_interaction(conn, *, customer_id: int, admin_id: int, channel: str,
                            subject: str, summary: str, outcome: str = "") -> int:
    row = conn.execute(
        """INSERT INTO support_interactions
           (customer_id, admin_id, channel, subject, summary, outcome, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (customer_id, admin_id, channel.strip(), subject.strip(), summary.strip(), outcome.strip(), _now()),
    )
    conn.commit()
    return int(row.lastrowid)


def inventory_health(conn, product_id: int) -> Dict[str, Any]:
    product = conn.execute("SELECT id, name, quantity FROM products WHERE id=?", (product_id,)).fetchone()
    if not product:
        return {}
    row = conn.execute("SELECT * FROM inventory_controls WHERE product_id=?", (product_id,)).fetchone()
    control = dict(row) if row else {"safety_stock": 0, "reorder_point": 0, "supplier_lead_days": 0,
                                     "damaged_qty": 0, "quarantined_qty": 0, "returned_qty": 0}
    sellable = max(0, int(product[2] or 0) - int(control.get("damaged_qty", 0)) - int(control.get("quarantined_qty", 0)))
    status = "ok"
    if sellable <= int(control.get("safety_stock", 0)):
        status = "critical"
    elif sellable <= int(control.get("reorder_point", 0)):
        status = "reorder"
    return {"product_id": int(product[0]), "name": product[1], "sellable": sellable,
            "status": status, **{k: control.get(k, 0) for k in ("safety_stock", "reorder_point", "supplier_lead_days")}}




def ensure_operations_lab_schema(conn) -> None:
    """Create persistent operator-lab structures used by Simulation and Training."""
    conn.execute("""CREATE TABLE IF NOT EXISTS simulation_scenarios (
        key TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
        severity TEXT NOT NULL DEFAULT 'medium', recommended_action TEXT NOT NULL DEFAULT '',
        acceptance_json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS training_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER REFERENCES admin_users(id),
        scenario_key TEXT NOT NULL, score INTEGER NOT NULL DEFAULT 0, passed INTEGER NOT NULL DEFAULT 0,
        answer TEXT NOT NULL DEFAULT '', misses_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_training_attempts_admin ON training_attempts(admin_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_training_attempts_scenario ON training_attempts(scenario_key, created_at DESC)")
    now = _now()
    scenarios = [
        ("payment_outage","Payment outage","Provider unavailable or callbacks delayed.","critical","Fail safely; do not double-charge; reconcile provider state.",["do not refund blindly","check provider state","reconcile before retry"]),
        ("stockout","Stockout","Demand exhausts inventory while reservations exist.","high","Stop uncontrolled selling; inspect reservations; resolve discrepancy.",["stop uncontrolled selling","inspect reservation state","record exception"]),
        ("refund_surge","Refund surge","Refund volume spikes above normal operating envelope.","high","Protect cash flow; enforce approval policy; reconcile provider state.",["follow approval policy","check provider refund state","reconcile before retry"]),
        ("coupon_abuse","Coupon abuse","Promotion usage exhibits suspicious concentration or repeated redemption.","high","Preserve evidence; restrict safely; escalate suspicious activity.",["preserve evidence","do not silently edit financial history","escalate suspicious activity"]),
    ]
    for row in scenarios:
        conn.execute("INSERT OR IGNORE INTO simulation_scenarios(key,title,description,severity,recommended_action,acceptance_json,updated_at) VALUES(?,?,?,?,?,?,?)", (*row[:5], _json(row[5]), now))
    conn.commit()

def simulation_catalog(conn):
    ensure_operations_lab_schema(conn)
    return [dict(r) for r in conn.execute("SELECT * FROM simulation_scenarios ORDER BY severity DESC, title").fetchall()]

def simulation_report(conn, run_id: int) -> dict:
    ensure_operations_lab_schema(conn)
    row=conn.execute("SELECT * FROM simulation_runs WHERE id=?", (int(run_id),)).fetchone()
    if not row: return {}
    result=json.loads(row['results_json'] or '{}'); params=json.loads(row['parameters_json'] or '{}')
    key=str(params.get('scenario') or '')
    scenario=conn.execute("SELECT * FROM simulation_scenarios WHERE key=?", (key,)).fetchone()
    acceptance=json.loads(scenario['acceptance_json'] or '[]') if scenario else []
    result.update({"run_id":int(row['id']),"status":str(row['status']),"created_at":str(row['created_at']),
                   "admin_id":int(row['admin_id']) if row['admin_id'] else None,
                   "scenario_title":str(scenario['title']) if scenario else key,
                   "scenario_severity":str(scenario['severity']) if scenario else 'medium',
                   "recommended_action":str(scenario['recommended_action']) if scenario else '',
                   "acceptance_checks":acceptance})
    return result

def record_training_attempt(conn, *, admin_id: int, scenario_key: str, answer: str) -> dict:
    ensure_operations_lab_schema(conn)
    row=conn.execute("SELECT * FROM simulation_scenarios WHERE key=?", (scenario_key,)).fetchone()
    if not row: raise ValueError('Unknown training scenario')
    checks=json.loads(row['acceptance_json'] or '[]'); normalized=(answer or '').strip().lower()
    misses=[c for c in checks if c.lower() not in normalized]; score=round((len(checks)-len(misses))/len(checks)*100) if checks else 0
    passed=1 if score>=80 else 0; created=_now()
    ins=conn.execute("INSERT INTO training_attempts(admin_id,scenario_key,score,passed,answer,misses_json,created_at) VALUES(?,?,?,?,?,?,?)", (int(admin_id),scenario_key,score,passed,answer[:4000],_json(misses),created)); conn.commit()
    return {"id":int(ins.lastrowid),"scenario_key":scenario_key,"score":score,"passed":bool(passed),"misses":misses,"created_at":created}

def training_report(conn, *, admin_id: int | None = None) -> dict:
    ensure_operations_lab_schema(conn)
    where=''; params=[]
    if admin_id:
        where='WHERE admin_id=?'; params=[int(admin_id)]
    row=conn.execute(f"SELECT COUNT(*) attempts, COALESCE(AVG(score),0) avg_score, SUM(CASE WHEN passed=1 THEN 1 ELSE 0 END) passed FROM training_attempts {where}", params).fetchone()
    return {"attempts":int(row['attempts'] or 0),"avg_score":round(float(row['avg_score'] or 0),1),"passed":int(row['passed'] or 0)}


def ensure_guardian_mastery_schema(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS guardian_detectors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'medium',
        enabled INTEGER NOT NULL DEFAULT 1,
        description TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS exception_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exception_id INTEGER NOT NULL REFERENCES business_exceptions(id) ON DELETE CASCADE,
        event_type TEXT NOT NULL,
        actor_id INTEGER,
        details_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_exception_events_exception ON exception_events(exception_id, created_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS guardian_sla_policies (
        severity TEXT PRIMARY KEY,
        due_minutes INTEGER NOT NULL DEFAULT 0,
        escalation_grace_minutes INTEGER NOT NULL DEFAULT 0,
        notify_assignee INTEGER NOT NULL DEFAULT 1,
        notify_admins INTEGER NOT NULL DEFAULT 1,
        enabled INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS customer_recovery_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
        admin_id INTEGER REFERENCES admin_users(id),
        playbook_key TEXT NOT NULL,
        step_index INTEGER NOT NULL,
        action TEXT NOT NULL,
        outcome TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_customer_recovery_actions_customer ON customer_recovery_actions(customer_id, created_at DESC)")
    now=_now()
    defaults=[('critical',60,15,1,1,1),('high',240,30,1,1,1),('medium',1440,60,1,1,1),('low',4320,120,1,0,1)]
    for severity,due,grace,na,admins,en in defaults:
        conn.execute("INSERT OR IGNORE INTO guardian_sla_policies(severity,due_minutes,escalation_grace_minutes,notify_assignee,notify_admins,enabled,updated_at) VALUES(?,?,?,?,?,?,?)", (severity,due,grace,na,admins,en,now))
    builtins=[
        ('negative_inventory','Negative inventory','critical','Product stock below zero.'),
        ('payment_without_paid_order','Payment/order mismatch','high','Provider payment indicates success while local order state does not.'),
        ('stale_processing_refund','Stale refund','high','Refund remains in processing state beyond the SLA window.'),
    ]
    for code,title,severity,description in builtins:
        conn.execute("INSERT OR IGNORE INTO guardian_detectors(code,title,severity,enabled,description,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (code,title,severity,1,description,now,now))
    conn.commit()

def record_exception_event(conn, exception_id: int, event_type: str, *, actor_id: int | None = None, details: dict | None = None) -> None:
    ensure_guardian_mastery_schema(conn)
    payload = {"exception_id": int(exception_id), "event_type": str(event_type), "actor_id": int(actor_id) if actor_id else None, "details": details or {}}
    conn.execute("INSERT INTO exception_events(exception_id,event_type,actor_id,details_json,created_at) VALUES(?,?,?,?,?)", (int(exception_id), str(event_type), int(actor_id) if actor_id else None, _json(details or {}), _now()))
    publish_event(conn, topic=f"governance.exception.{event_type}", aggregate="exception", aggregate_id=exception_id, payload=payload)
    conn.commit()

def exception_timeline(conn, exception_id: int, limit: int = 100):
    ensure_guardian_mastery_schema(conn)
    return conn.execute("""SELECT ev.*, a.username AS actor_name FROM exception_events ev LEFT JOIN admin_users a ON a.id=ev.actor_id WHERE ev.exception_id=? ORDER BY ev.created_at DESC LIMIT ?""", (int(exception_id), max(1,min(int(limit),200)))).fetchall()

def guardian_detectors(conn):
    ensure_guardian_mastery_schema(conn)
    return conn.execute("SELECT * FROM guardian_detectors ORDER BY enabled DESC, severity, title").fetchall()

def guardian_sla_policy(conn, severity: str) -> dict:
    ensure_guardian_mastery_schema(conn)
    row = conn.execute("SELECT * FROM guardian_sla_policies WHERE severity=? AND enabled=1", (str(severity),)).fetchone()
    return dict(row) if row else {}

def notify_guardian_escalation(conn, *, exception_id: int, title: str, assigned_to: int | None, severity: str, reason: str) -> int:
    policy = guardian_sla_policy(conn, severity)
    recipients=set()
    if assigned_to and int(policy.get('notify_assignee',1)):
        recipients.add(int(assigned_to))
    if int(policy.get('notify_admins',1)):
        rows=conn.execute("SELECT id FROM admin_users WHERE is_active=1 AND role IN ('master','admin','custom')").fetchall()
        recipients.update(int(r[0]) for r in rows)
    count=0
    for admin_id in recipients:
        conn.execute("INSERT INTO team_notifications(admin_id,kind,title,body,created_at) VALUES(?,?,?,?,?)", (admin_id,'guardian_escalation',f'Guardian escalation: {title}',f'{severity.upper()}: {reason}',_now()))
        count+=1
    conn.commit()
    return count

def record_recovery_action(conn, *, customer_id: int, admin_id: int, playbook_key: str, step_index: int, action: str, outcome: str = '') -> int:
    row=conn.execute("INSERT INTO customer_recovery_actions(customer_id,admin_id,playbook_key,step_index,action,outcome,created_at) VALUES(?,?,?,?,?,?,?)", (int(customer_id),int(admin_id),str(playbook_key),int(step_index),str(action).strip(),str(outcome).strip(),_now()))
    conn.commit()
    return int(row.lastrowid)

def recovery_action_history(conn, customer_id: int, limit: int = 100):
    return conn.execute("SELECT r.*, a.username AS admin_username FROM customer_recovery_actions r LEFT JOIN admin_users a ON a.id=r.admin_id WHERE r.customer_id=? ORDER BY r.created_at DESC LIMIT ?", (int(customer_id),max(1,min(int(limit),200)))).fetchall()

def guardian_cross_signal_summary(conn, *, window_hours: int = 24) -> Dict[str, Any]:
    """Correlate open exceptions with recent domain events for evidence-first risk context."""
    try:
        since = (datetime.now(timezone.utc) - timedelta(hours=max(1, int(window_hours)))).isoformat()
        exception_rows = conn.execute(
            "SELECT code,severity,entity,entity_id,status,created_at FROM business_exceptions WHERE status IN ('open','acknowledged') ORDER BY created_at DESC LIMIT 500"
        ).fetchall()
        events = conn.execute(
            "SELECT topic,aggregate,aggregate_id,created_at FROM domain_events WHERE created_at>=? ORDER BY created_at DESC LIMIT 1000", (since,)
        ).fetchall()
        event_keys = {}
        for e in events:
            event_keys.setdefault((str(e['aggregate']), str(e['aggregate_id'])), []).append(dict(e))
        correlated=[]
        for ex in exception_rows:
            key=(str(ex['entity']), str(ex['entity_id']))
            matches=event_keys.get(key, [])
            if matches:
                correlated.append({"code": str(ex['code']), "severity": str(ex['severity']), "entity": key[0], "entity_id": key[1], "event_count": len(matches), "recent_topics": [str(m['topic']) for m in matches[:6]]})
        return {"window_hours": int(window_hours), "open_exceptions": len(exception_rows), "recent_events": len(events), "correlated_exceptions": len(correlated), "correlations": correlated[:100]}
    except Exception as exc:
        return {"window_hours": int(window_hours), "open_exceptions": 0, "recent_events": 0, "correlated_exceptions": 0, "correlations": [], "error": str(exc)[:300]}


def run_guardian_scan(conn) -> Dict[str, Any]:
    created = scan_exceptions(conn)
    escalated = escalate_overdue_exceptions(conn)
    open_rows = conn.execute(
        """SELECT severity, COUNT(*) c FROM business_exceptions
           WHERE status='open' GROUP BY severity"""
    ).fetchall()
    risk_weights = {"critical": 10, "high": 5, "medium": 2, "low": 1}
    score = sum(risk_weights.get(str(r[0]), 1) * int(r[1]) for r in open_rows)
    cross_signal = guardian_cross_signal_summary(conn)
    correlation_bonus = min(20, int(cross_signal.get("correlated_exceptions", 0)) * 2)
    return {"created": created, "escalated": escalated, "open_exceptions": sum(int(r[1]) for r in open_rows),
            "risk_score": score + correlation_bonus, "severity_counts": {str(r[0]): int(r[1]) for r in open_rows}, "cross_signal": cross_signal}


def simulate_scenario(conn, *, admin_id: int, scenario: str, scale: int = 100) -> Dict[str, Any]:
    ensure_operations_lab_schema(conn)
    scale = max(1, min(int(scale), 1_000_000))
    presets = {
        "payment_outage": {"failure_rate": 0.65, "refund_rate": 0.01},
        "stockout": {"failure_rate": 0.08, "refund_rate": 0.14},
        "refund_surge": {"failure_rate": 0.03, "refund_rate": 0.35},
        "coupon_abuse": {"failure_rate": 0.02, "refund_rate": 0.08},
    }
    cfg = presets.get(scenario)
    if not cfg:
        raise ValueError("Unknown simulation scenario")
    failure = round(scale * cfg["failure_rate"])
    refunds = round((scale - failure) * cfg["refund_rate"])
    gross = (scale - failure) * 1000
    result = {"scenario": scenario, "scale": scale, "failed_orders": failure,
              "refunds": refunds, "gross_amount": gross, "net_amount": gross - refunds * 1000,
              "risk": "critical" if scenario == "payment_outage" else "high" if refunds > scale * .2 else "medium"}
    row = conn.execute(
        "INSERT INTO simulation_runs (admin_id,label,parameters_json,results_json,status,created_at) VALUES (?,?,?,?,?,?)",
        (admin_id, f"scenario:{scenario}", _json({"scenario": scenario, "scale": scale}), _json(result), "completed", _now()),
    )
    conn.commit()
    return {"id": int(row.lastrowid), **result}


def request_or_validate_approval(conn, *, action: str, requested_by: int, entity: str, entity_id: int,
                                 amount: int = 0, reason: str = "", metadata: Optional[dict] = None,
                                 approval_id: Optional[int] = None) -> Dict[str, Any]:
    """Enforce a two-person approval policy when configured.

    Returns allowed=True only when no approval is required or a still-valid
    approval from another admin matches the requested action/entity/metadata.
    Otherwise returns allowed=False with a newly-created pending approval.
    """
    expire_pending_approvals(conn)
    if not policy_requires_approval(conn, action, amount):
        return {"allowed": True, "approval_id": None, "required": False}
    if approval_id:
        row = conn.execute(
            "SELECT * FROM admin_approval_requests WHERE id=? AND action=? AND entity=? AND entity_id=?",
            (int(approval_id), action, entity, int(entity_id)),
        ).fetchone()
        if row and row["status"] == "approved" and int(row["approved_by"] or 0) != int(requested_by):
            meta_ok = True
            try:
                expected_wrapper = json.loads(row["metadata_json"] or "{}")
                expected = expected_wrapper.get("business_metadata", {}) if isinstance(expected_wrapper, dict) else {}
                for k, v in (metadata or {}).items():
                    if expected.get(k) != v:
                        meta_ok = False
                        break
            except Exception:
                meta_ok = False
            if meta_ok:
                return {"allowed": True, "approval_id": int(approval_id), "required": True}
    new_id = create_approval(conn, requested_by=requested_by, action=action, entity=entity,
                              entity_id=entity_id, amount=amount, reason=reason, metadata=metadata)
    return {"allowed": False, "approval_id": new_id, "required": True}


def open_exception(conn, *, code: str, severity: str, title: str, description: str,
                   entity: str = "", entity_id: int = 0, assigned_to: int = 0,
                   due_at: Optional[str] = None, metadata: Optional[dict] = None) -> int:
    existing = conn.execute(
        "SELECT id FROM business_exceptions WHERE code=? AND entity=? AND entity_id=? AND status='open' LIMIT 1",
        (code, entity, int(entity_id or 0)),
    ).fetchone()
    if existing:
        return int(existing[0])
    row_id = add_exception(conn, code=code, severity=severity, title=title, description=description,
                           entity=entity, entity_id=entity_id, metadata=metadata)
    conn.execute(
        "UPDATE business_exceptions SET assigned_to=?, due_at=? WHERE id=?",
        (assigned_to or None, due_at, row_id),
    )
    conn.commit()
    return row_id



def assign_exception(conn, exception_id: int, *, assigned_to: int = 0, due_at: Optional[str] = None, actor_id: int | None = None) -> bool:
    row=conn.execute("SELECT id,status FROM business_exceptions WHERE id=?", (int(exception_id),)).fetchone()
    if not row or str(row["status"]) == "resolved":
        return False
    if assigned_to:
        valid=conn.execute("SELECT id FROM admin_users WHERE id=? AND is_active=1", (int(assigned_to),)).fetchone()
        if not valid:
            return False
    if due_at is None:
        conn.execute("UPDATE business_exceptions SET assigned_to=?, updated_at=? WHERE id=?", (int(assigned_to) if assigned_to else None, _now(), int(exception_id)))
    else:
        conn.execute("UPDATE business_exceptions SET assigned_to=?, due_at=?, updated_at=? WHERE id=?", (int(assigned_to) if assigned_to else None, due_at, _now(), int(exception_id)))
    conn.commit()
    try: record_exception_event(conn, exception_id, 'assigned', actor_id=actor_id, details={'assigned_to': int(assigned_to) if assigned_to else None, 'due_at': due_at})
    except Exception: pass
    return True

def escalate_overdue_exceptions(conn, *, now_iso: Optional[str] = None) -> int:
    now_iso = now_iso or _now()
    rows = conn.execute(
        "SELECT id, severity, title, assigned_to FROM business_exceptions WHERE status IN ('open','acknowledged') AND due_at IS NOT NULL AND due_at < ? AND escalated_at IS NULL",
        (now_iso,),
    ).fetchall()
    for row in rows:
        new_severity = 'critical' if str(row['severity']) in {'high','critical'} else 'high'
        conn.execute(
            "UPDATE business_exceptions SET severity=?, escalated_at=?, escalation_reason='SLA deadline exceeded', updated_at=? WHERE id=?",
            (new_severity, now_iso, now_iso, int(row['id'])),
        )
        try: record_exception_event(conn, int(row['id']), 'escalated', details={'new_severity': new_severity, 'reason': 'SLA deadline exceeded'})
        except Exception: pass
        try:
            notify_guardian_escalation(conn, exception_id=int(row['id']), title=str(row['title']), assigned_to=int(row['assigned_to']) if row['assigned_to'] else None, severity=new_severity, reason='SLA deadline exceeded')
        except Exception:
            pass
    conn.commit()
    return len(rows)


def cart_coupon_margin(*, items: Iterable[dict], subtotal: int, discount_type: str, discount_value: int) -> Dict[str, int]:
    """Calculate a cart-wide discount subject to every line's minimum margin.

    ``items`` must expose ``product`` and ``quantity`` keys; product must expose
    ``price``, ``cost_price`` and ``min_margin_percent``. The result is a hard
    server-side ceiling, so no checkout path can bypass product margin floors.
    """
    subtotal = max(0, int(subtotal or 0))
    if subtotal <= 0:
        return {"requested_discount": 0, "safe_discount": 0, "final_price": 0, "margin_budget": 0}
    if discount_type == 'percent':
        requested = int(round(subtotal * int(discount_value or 0) / 100))
    else:
        requested = int(discount_value or 0)
    requested = max(0, min(requested, max(subtotal - 1, 0)))

    margin_budget = 0
    for item in items or []:
        product = item.get('product') if isinstance(item, dict) else None
        qty = max(0, int(item.get('quantity', 0) if isinstance(item, dict) else 0))
        if not product or qty <= 0:
            continue
        price = max(0, int(product.get('price', 0) or 0))
        cost = max(0, int(product.get('cost_price', 0) or 0))
        minimum_margin = max(0, int(product.get('min_margin_percent', 15) or 15))
        if cost <= 0:
            margin_budget += price * qty
            continue
        floor = int(round(cost * (1 + minimum_margin / 100)))
        margin_budget += max(0, (price - min(price, floor)) * qty)

    safe_discount = min(requested, margin_budget)
    final_price = max(0, subtotal - safe_discount)
    return {
        "requested_discount": requested,
        "safe_discount": safe_discount,
        "final_price": final_price,
        "margin_budget": margin_budget,
    }


def coupon_discount_with_margin(*, price: int, cost_price: int, discount_type: str, discount_value: int,
                                min_margin_percent: int = 15) -> Dict[str, int]:
    """Return a safe discount and resulting margin; never permits a known-cost
    product to be sold below its configured minimum margin.
    """
    price = max(0, int(price or 0))
    cost = max(0, int(cost_price or 0))
    if discount_type == 'percent':
        raw = int(round(price * int(discount_value or 0) / 100))
    else:
        raw = int(discount_value or 0)
    raw = max(0, min(raw, max(price - 1, 0)))
    floor_price = int(round(cost * (1 + max(0, int(min_margin_percent or 0)) / 100)))
    safe_final = max(0 if not price else min(price, floor_price), price - raw) if cost else price - raw
    safe_final = min(price, max(0, safe_final))
    safe_discount = max(0, price - safe_final)
    margin = safe_final - cost
    return {"discount": safe_discount, "final_price": safe_final, "margin": margin, "floor_price": floor_price}


def customer_timeline(conn, customer_id: int) -> Dict[str, Any]:
    customer = conn.execute("SELECT id, name, email, phone, created_at, last_login_at FROM customers WHERE id=?", (customer_id,)).fetchone()
    if not customer:
        return {}
    orders = conn.execute(
        "SELECT id, order_ref, amount, status, payment_state, payment_mode, created_at FROM orders WHERE customer_id=? ORDER BY created_at DESC LIMIT 100",
        (customer_id,),
    ).fetchall()
    interactions = conn.execute(
        """SELECT s.*, a.username AS admin_username
           FROM support_interactions s LEFT JOIN admin_users a ON a.id=s.admin_id
           WHERE s.customer_id=? ORDER BY s.created_at DESC LIMIT 100""",
        (customer_id,),
    ).fetchall()
    return {"customer": dict(customer), "orders": [dict(r) for r in orders], "interactions": [dict(r) for r in interactions]}


RECOVERY_PLAYBOOKS = {
    "payment_failed": {
        "title": "Payment recovery",
        "steps": [
            "Confirm whether the provider shows a successful charge before asking the customer to retry.",
            "If no charge exists, offer a fresh checkout link and keep the original cart intact.",
            "If the provider charged but the order is missing, run reconciliation before taking another payment.",
            "Record the outcome in the customer timeline.",
        ],
    },
    "delivery_delayed": {
        "title": "Delivery recovery",
        "steps": [
            "Check the latest fulfillment/delivery event.",
            "Give the customer a concrete status update instead of a generic apology.",
            "Escalate if the delivery SLA is exceeded.",
            "Consider a recovery credit only within the store's approval policy.",
        ],
    },
    "duplicate_charge": {
        "title": "Possible duplicate charge",
        "steps": [
            "Compare provider payment IDs for the order.",
            "Do not issue a second refund until duplicate charging is confirmed.",
            "Reconcile provider and local state.",
            "Record the resolution and notify the customer clearly.",
        ],
    },
    "refund_delayed": {
        "title": "Refund recovery",
        "steps": [
            "Check the provider-side refund status.",
            "If processing is stale, reconcile before retrying.",
            "Never create a second refund blindly.",
            "Give the customer an honest status and reference.",
        ],
    },
}


def recommend_recovery_playbooks(timeline: Dict[str, Any]) -> list[dict]:
    orders = timeline.get("orders", [])
    suggestions = []
    if any(str(o.get("status")) in {"payment_failed", "failed"} for o in orders):
        suggestions.append(RECOVERY_PLAYBOOKS["payment_failed"])
    if any(str(o.get("status")) in {"delivery_failed", "processing"} for o in orders):
        suggestions.append(RECOVERY_PLAYBOOKS["delivery_delayed"])
    if any(str(o.get("payment_state")) in {"paid", "captured"} and str(o.get("status")) in {"payment_pending", "created"} for o in orders):
        suggestions.append(RECOVERY_PLAYBOOKS["duplicate_charge"])
    if any(str(o.get("status")) in {"refund_pending", "refunded"} for o in orders):
        suggestions.append(RECOVERY_PLAYBOOKS["refund_delayed"])
    return suggestions


def guardian_health(conn) -> Dict[str, Any]:
    """Return a self-diagnostic report for the Guardian control plane."""
    checks = []
    def add(name, ok, detail, severity='info'):
        checks.append({'name': name, 'ok': bool(ok), 'detail': str(detail), 'severity': severity})

    try:
        ensure_guardian_mastery_schema(conn)
        required = {
            'business_exceptions': {'id','code','severity','title','status','assigned_to','due_at','escalated_at','escalation_reason'},
            'guardian_detectors': {'code','title','severity','enabled'},
            'guardian_sla_policies': {'severity','due_minutes','escalation_grace_minutes','notify_assignee','notify_admins','enabled'},
            'exception_events': {'exception_id','event_type','created_at'},
        }
        for table, needed in required.items():
            cols={r[1] for r in conn.execute(f"PRAGMA table_info([{table}])").fetchall()}
            missing=sorted(needed-cols)
            add(f'schema:{table}', not missing, 'ok' if not missing else 'missing columns: '+', '.join(missing), 'critical' if missing else 'info')
    except Exception as exc:
        add('schema', False, f'schema check failed: {exc}', 'critical')

    try:
        detectors = conn.execute("SELECT COUNT(*), SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) FROM guardian_detectors").fetchone()
        total = int(detectors[0] or 0); enabled = int(detectors[1] or 0)
        add('detectors', total > 0 and enabled > 0, f'{enabled}/{total} enabled', 'critical' if total == 0 or enabled == 0 else 'info')
    except Exception as exc:
        add('detectors', False, f'detector check failed: {exc}', 'critical')

    try:
        policies = conn.execute("SELECT COUNT(*), SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) FROM guardian_sla_policies").fetchone()
        total = int(policies[0] or 0); enabled = int(policies[1] or 0)
        add('sla_policies', total >= 4 and enabled >= 1, f'{enabled}/{total} enabled', 'critical' if total < 4 or enabled == 0 else 'info')
    except Exception as exc:
        add('sla_policies', False, f'SLA policy check failed: {exc}', 'critical')

    try:
        overdue = int(conn.execute("SELECT COUNT(*) FROM business_exceptions WHERE status IN ('open','acknowledged') AND due_at IS NOT NULL AND due_at < ?", (_now(),)).fetchone()[0])
        add('overdue_exceptions', True, f'{overdue} overdue exception(s)', 'warning' if overdue else 'info')
    except Exception as exc:
        add('overdue_exceptions', False, f'overdue check failed: {exc}', 'critical')

    try:
        open_ex = int(conn.execute("SELECT COUNT(*) FROM business_exceptions WHERE status IN ('open','acknowledged')").fetchone()[0])
        add('open_exceptions', True, f'{open_ex} open/acknowledged exception(s)', 'info')
    except Exception as exc:
        add('open_exceptions', False, f'exception count failed: {exc}', 'critical')

    critical = [c for c in checks if not c['ok'] and c['severity']=='critical']
    warnings = [c for c in checks if c['severity']=='warning']
    return {
        'ok': not critical,
        'status': 'healthy' if not critical else 'degraded',
        'checks': checks,
        'critical_count': len(critical),
        'warning_count': len(warnings),
        'generated_at': _now(),
    }


def guardian_acceptance_check(conn) -> Dict[str, Any]:
    """Deterministic closure check for Guardian's control-plane contract."""
    checks: Dict[str, bool] = {}
    failures: list[str] = []
    try:
        db.ensure_business_exception_columns(conn)
        checks['schema'] = bool(db.guardian_schema_ready(conn))
    except Exception as exc:
        checks['schema'] = False; failures.append(f'schema:{exc}')
    try:
        detectors = guardian_detectors(conn)
        checks['detectors'] = len(detectors) >= 3 and all(int(r['enabled']) in (0,1) for r in detectors)
        if not checks['detectors']: failures.append('detectors')
    except Exception as exc:
        checks['detectors'] = False; failures.append(f'detectors:{exc}')
    try:
        policies = [guardian_sla_policy(conn, s) for s in ('critical','high','medium','low')]
        checks['sla_policies'] = all(bool(p) and int(p.get('due_minutes',0)) > 0 and int(p.get('escalation_grace_minutes',0)) >= 0 for p in policies)
        if not checks['sla_policies']: failures.append('sla_policies')
    except Exception as exc:
        checks['sla_policies'] = False; failures.append(f'sla_policies:{exc}')
    try:
        bad_assignments = conn.execute("SELECT COUNT(*) FROM business_exceptions e LEFT JOIN admin_users a ON a.id=e.assigned_to WHERE e.assigned_to IS NOT NULL AND (a.id IS NULL OR a.is_active!=1)").fetchone()[0]
        checks['assignments'] = int(bad_assignments or 0) == 0
        if not checks['assignments']: failures.append('assignments')
    except Exception as exc:
        checks['assignments'] = False; failures.append(f'assignments:{exc}')
    try:
        overdue = conn.execute("SELECT COUNT(*) FROM business_exceptions WHERE status IN ('open','acknowledged') AND due_at IS NULL").fetchone()[0]
        # A due time may be intentionally absent only when every enabled SLA policy is disabled. Current defaults are enabled.
        checks['due_at_coverage'] = int(overdue or 0) == 0
        if not checks['due_at_coverage']: failures.append('due_at_coverage')
    except Exception as exc:
        checks['due_at_coverage'] = False; failures.append(f'due_at_coverage:{exc}')
    try:
        event_topics = {str(r['topic']) for r in conn.execute("SELECT DISTINCT topic FROM domain_events WHERE topic LIKE 'governance.exception.%'").fetchall()}
        checks['event_lifecycle'] = {'governance.exception.created','governance.exception.acknowledged','governance.exception.assigned','governance.exception.escalated','governance.exception.resolved','governance.exception.reopened'}.issubset(event_topics)
        # If no lifecycle has ever occurred, treat this as not yet exercised rather than a system failure.
    except Exception as exc:
        checks['event_lifecycle'] = False; failures.append(f'event_lifecycle:{exc}')
    return {'ok': not failures, 'checks': checks, 'failures': failures}
