"""TITAN institutional-governance services.

Small, dependency-free business-control primitives: approvals, exceptions,
reconciliation snapshots, decision journal and SOP records.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

import database as db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def create_approval(conn, *, requested_by: int, action: str, entity: str,
                    entity_id: int, amount: int = 0, reason: str = "",
                    metadata: Optional[dict] = None) -> int:
    row = conn.execute(
        """INSERT INTO admin_approval_requests
           (request_ref, requested_by, action, entity, entity_id, amount, reason,
            metadata_json, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (f"APR-{secrets.token_hex(6).upper()}", requested_by, action, entity,
         entity_id, int(amount or 0), reason.strip(), _json(metadata), _now(), _now()),
    )
    conn.commit()
    return int(row.lastrowid)


def approve(conn, approval_id: int, *, approved_by: int, note: str = "") -> bool:
    row = conn.execute(
        "SELECT * FROM admin_approval_requests WHERE id = ?",
        (approval_id,),
    ).fetchone()
    if not row or row["status"] != "pending" or int(row["requested_by"]) == int(approved_by):
        return False
    policy = conn.execute(
        "SELECT approval_expiry_minutes FROM high_risk_action_policies WHERE action=?",
        (row["action"],),
    ).fetchone()
    if policy:
        try:
            created = datetime.fromisoformat(row["created_at"])
            age_seconds = (datetime.now(timezone.utc) - (created if created.tzinfo else created.replace(tzinfo=timezone.utc))).total_seconds()
            if age_seconds > int(policy[0] or 1440) * 60:
                conn.execute("UPDATE admin_approval_requests SET status='expired', updated_at=? WHERE id=? AND status='pending'", (_now(), approval_id))
                conn.commit()
                return False
        except (TypeError, ValueError):
            return False
    conn.execute(
        """UPDATE admin_approval_requests
           SET status='approved', approved_by=?, approval_note=?, approved_at=?, updated_at=?
           WHERE id=? AND status='pending'""",
        (approved_by, note.strip(), _now(), _now(), approval_id),
    )
    conn.commit()
    return True


def reject(conn, approval_id: int, *, rejected_by: int, note: str = "") -> bool:
    row = conn.execute("SELECT status FROM admin_approval_requests WHERE id = ?", (approval_id,)).fetchone()
    if not row or row["status"] != "pending":
        return False
    conn.execute(
        """UPDATE admin_approval_requests
           SET status='rejected', approved_by=?, approval_note=?, approved_at=?, updated_at=?
           WHERE id=? AND status='pending'""",
        (rejected_by, note.strip(), _now(), _now(), approval_id),
    )
    conn.commit()
    return True


def add_exception(conn, *, code: str, severity: str, title: str, description: str,
                  entity: str = "", entity_id: int = 0, metadata: Optional[dict] = None) -> int:
    row = conn.execute(
        """INSERT INTO business_exceptions
           (code, severity, title, description, entity, entity_id, metadata_json,
            status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
        (code, severity, title, description, entity, int(entity_id or 0), _json(metadata), _now(), _now()),
    )
    conn.commit()
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
    return True


def create_decision(conn, *, admin_id: int, title: str, decision: str,
                    reason: str = "", expected_result: str = "") -> int:
    row = conn.execute(
        """INSERT INTO decision_journal
           (admin_id, title, decision, reason, expected_result, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (admin_id, title.strip(), decision.strip(), reason.strip(), expected_result.strip(), _now()),
    )
    conn.commit()
    return int(row.lastrowid)


def create_sop(conn, *, admin_id: int, title: str, trigger: str, steps: Iterable[str]) -> int:
    row = conn.execute(
        """INSERT INTO sop_documents
           (title, trigger, steps_json, version, created_by, active, created_at, updated_at)
           VALUES (?, ?, ?, 1, ?, 1, ?, ?)""",
        (title.strip(), trigger.strip(), _json(list(steps)), admin_id, _now(), _now()),
    )
    conn.commit()
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
    conn.execute("UPDATE audit_integrity SET last_hash=? WHERE id=1", (digest,))
    return digest


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


def run_guardian_scan(conn) -> Dict[str, Any]:
    created = scan_exceptions(conn)
    escalated = escalate_overdue_exceptions(conn)
    open_rows = conn.execute(
        """SELECT severity, COUNT(*) c FROM business_exceptions
           WHERE status='open' GROUP BY severity"""
    ).fetchall()
    risk_weights = {"critical": 10, "high": 5, "medium": 2, "low": 1}
    score = sum(risk_weights.get(str(r[0]), 1) * int(r[1]) for r in open_rows)
    return {"created": created, "escalated": escalated, "open_exceptions": sum(int(r[1]) for r in open_rows),
            "risk_score": score, "severity_counts": {str(r[0]): int(r[1]) for r in open_rows}}


def simulate_scenario(conn, *, admin_id: int, scenario: str, scale: int = 100) -> Dict[str, Any]:
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
                expected = json.loads(row["metadata_json"] or "{}")
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


def escalate_overdue_exceptions(conn, *, now_iso: Optional[str] = None) -> int:
    now_iso = now_iso or _now()
    rows = conn.execute(
        "SELECT id, severity, title FROM business_exceptions WHERE status='open' AND due_at IS NOT NULL AND due_at < ? AND escalated_at IS NULL",
        (now_iso,),
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE business_exceptions SET severity=CASE WHEN severity='critical' THEN 'critical' WHEN severity='high' THEN 'critical' ELSE 'high' END, escalated_at=?, escalation_reason='SLA deadline exceeded', updated_at=? WHERE id=?",
            (now_iso, now_iso, int(row[0])),
        )
    conn.commit()
    return len(rows)


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
