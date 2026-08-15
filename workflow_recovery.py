"""Operational controls for durable workflow recovery."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Any

from titan_workflows import DurableWorkflow

def _now():
    return datetime.now(timezone.utc).isoformat()

def list_recoverable(conn, *, limit=200, stale_minutes=15):
    cutoff=(datetime.now(timezone.utc)-timedelta(minutes=int(stale_minutes))).isoformat()
    rows=conn.execute(
        """SELECT * FROM workflow_runs
           WHERE status IN ('failed','waiting')
              OR (status='running' AND updated_at < ?)
           ORDER BY updated_at ASC LIMIT ?""", (cutoff, max(1,min(int(limit),500)))) .fetchall()
    return [dict(r) for r in rows]

def recover_payment_workflow(conn, workflow_id: str, *, confirm_callable) -> dict[str, Any]:
    """Resume the known-safe payment confirmation workflow in-place.

    No new payment is created. The existing order finalizer remains the only
    authority for financial mutations and its idempotency protections apply.
    """
    row=conn.execute("SELECT * FROM workflow_runs WHERE workflow_id=?", (workflow_id,)).fetchone()
    if not row:
        raise ValueError("workflow not found")
    if row["workflow_type"] != "order.payment_confirmation":
        raise ValueError("workflow type is not recoverable through this action")
    if row["status"] not in ("failed","waiting","running"):
        raise ValueError(f"workflow is {row['status']}")
    order=conn.execute("SELECT * FROM orders WHERE order_ref=?", (row["aggregate_id"],)).fetchone()
    if not order:
        raise ValueError("order for workflow was not found")
    items=conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (order["id"],)).fetchall()

    def finalize(_conn, ctx):
        msg=confirm_callable(
            _conn, order, items,
            payment_mode=order["payment_mode"] or "gateway",
            razorpay_payment_id=order["razorpay_payment_id"] or None,
            razorpay_signature="",
        )
        return {"order_id": int(order["id"]), "order_ref": order["order_ref"], "delivery_message": msg or ""}

    from titan_workflows import DurableWorkflow, WorkflowStep
    context={"recovery": True, "recovered_at": _now()}
    previous={}
    try:
        import json
        previous=json.loads(row["context_json"] or "{}")
    except Exception:
        previous={}
    context.update(previous)
    flow=DurableWorkflow(
        conn, workflow_type=row["workflow_type"], aggregate_type=row["aggregate_type"],
        aggregate_id=row["aggregate_id"], workflow_id=workflow_id,
    )
    return flow.run([WorkflowStep("finalize_payment_and_fulfillment", finalize)], context=context)


def workflow_health(conn, *, stale_minutes=15):
    cutoff=(datetime.now(timezone.utc)-timedelta(minutes=int(stale_minutes))).isoformat()
    rows=conn.execute(
        "SELECT status, COUNT(*) AS n FROM workflow_runs WHERE status IN ('failed','waiting') OR (status='running' AND updated_at < ?) GROUP BY status",
        (cutoff,),
    ).fetchall()
    counts={"failed":0,"waiting":0,"stale_running":0}
    for row in rows:
        status=row["status"]
        if status in counts: counts[status]=int(row["n"] or 0)
        elif status == "running": counts["stale_running"]=int(row["n"] or 0)
    return {"recoverable": sum(counts.values()), **counts, "stale_minutes": int(stale_minutes)}


KNOWN_RECOVERABLE_WORKFLOWS = {
    "order.payment_confirmation",
    "order.refund",
    "order.delivery",
}


def recover_known_workflow(conn, workflow_id: str, *, confirm_callable=None,
                           notify_email_callable=None, notify_sms_callable=None,
                           provider_refund_id=None) -> dict[str, Any]:
    """Recover any supported critical commerce workflow in-place.

    Recovery never creates a second business operation. It resumes the existing
    workflow id and relies on the existing idempotent business finalizers.
    """
    row = conn.execute("SELECT * FROM workflow_runs WHERE workflow_id=?", (workflow_id,)).fetchone()
    if not row:
        raise ValueError("workflow not found")
    workflow_type = str(row["workflow_type"] or "")
    if workflow_type not in KNOWN_RECOVERABLE_WORKFLOWS:
        raise ValueError("workflow type is not supported for operator recovery")
    if row["status"] not in ("failed", "waiting", "running"):
        raise ValueError(f"workflow is {row['status']}")

    if workflow_type == "order.payment_confirmation":
        if confirm_callable is None:
            raise ValueError("payment recovery requires the authoritative payment finalizer")
        return recover_payment_workflow(conn, workflow_id, confirm_callable=confirm_callable)

    if workflow_type == "order.refund":
        from payment.refund import process_refund
        result = process_refund(conn, int(row["aggregate_id"]), provider_refund_id=provider_refund_id)
        return {"workflow_id": workflow_id, **(result if isinstance(result, dict) else {"result": result})}

    # Delivery has three steps. Re-run only the unfinished step(s); the durable
    # workflow engine skips completed steps, preserving the no-duplicate effect.
    order = conn.execute("SELECT * FROM orders WHERE order_ref=?", (row["aggregate_id"],)).fetchone()
    if not order:
        raise ValueError("order for delivery workflow was not found")
    from commerce_workflows import deliver_order_durable
    message = order["delivery_message"] or ""
    return deliver_order_durable(conn, order=order, delivery_message=message,
                                 notify_email_callable=notify_email_callable,
                                 notify_sms_callable=notify_sms_callable)
