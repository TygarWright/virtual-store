"""Canonical durable workflows for critical commerce transitions."""
from __future__ import annotations

from titan_workflows import DurableWorkflow, WorkflowStep, WorkflowError


def confirm_order_payment_durable(conn, *, order, order_items, payment_mode="gateway", razorpay_payment_id=None, razorpay_signature=None, confirm_callable):
    """Run the existing idempotent payment finalizer under durable workflow control.

    The legacy finalizer remains authoritative for business-state mutations; the
    workflow adds resumability, auditability and step state without duplicating
    financial logic.
    """
    flow = DurableWorkflow(conn, workflow_type="order.payment_confirmation", aggregate_type="order", aggregate_id=order["order_ref"])

    def finalize(_conn, ctx):
        msg = confirm_callable(_conn, order, order_items, payment_mode=payment_mode,
                               razorpay_payment_id=razorpay_payment_id, razorpay_signature=razorpay_signature)
        return {"order_id": int(order["id"]), "order_ref": order["order_ref"], "payment_mode": payment_mode, "delivery_message": msg or ""}

    try:
        return flow.run([WorkflowStep("finalize_payment_and_fulfillment", finalize)])
    except WorkflowError:
        raise


def workflow_status(conn, order_ref: str) -> dict:
    row = conn.execute("SELECT workflow_id FROM workflow_runs WHERE workflow_type='order.payment_confirmation' AND aggregate_type='order' AND aggregate_id=? ORDER BY updated_at DESC LIMIT 1", (str(order_ref),)).fetchone()
    if not row:
        return {"workflow_id": None, "status": "not_started", "context": {}}
    return DurableWorkflow(conn, workflow_type="order.payment_confirmation", aggregate_type="order", aggregate_id=order_ref, workflow_id=row["workflow_id"]).status()


def deliver_order_durable(conn, *, order, delivery_message, notify_email_callable=None, notify_sms_callable=None):
    """Mark an order delivered and notify the customer through a durable workflow.

    Notification callables are supplied by the existing application layer so this
    workflow does not own transport/provider logic. A completed notification step
    is never re-run when the workflow is resumed.
    """
    flow = DurableWorkflow(conn, workflow_type="order.delivery", aggregate_type="order", aggregate_id=order["order_ref"])

    def mark_delivered(_conn, ctx):
        current = _conn.execute("SELECT status, delivery_message, delivered_at FROM orders WHERE id=?", (int(order["id"]),)).fetchone()
        if not current:
            raise WorkflowError("order no longer exists")
        changed = current["status"] != "delivered"
        if changed:
            _conn.execute("UPDATE orders SET status='delivered', delivery_message=?, delivered_at=? WHERE id=?",
                           (delivery_message[:4000], db_now(_conn), int(order["id"])))
        try:
            from backend_kernel import publish_business_event
            publish_business_event(_conn, topic="order.delivered", aggregate="order", aggregate_id=int(order["id"]),
                                   payload={"order_id": int(order["id"]), "order_ref": str(order["order_ref"]), "changed": changed})
        except Exception:
            pass
        _conn.commit()
        return {"order_id": int(order["id"]), "order_ref": order["order_ref"], "delivered": True}

    def notify_email(_conn, ctx):
        if not notify_email_callable:
            return {"email_sent": False}
        notify_email_callable()
        return {"email_sent": True}

    def notify_sms(_conn, ctx):
        if not notify_sms_callable:
            return {"sms_sent": False}
        notify_sms_callable()
        return {"sms_sent": True}

    return flow.run([
        WorkflowStep("mark_order_delivered", mark_delivered),
        WorkflowStep("notify_customer_email", notify_email),
        WorkflowStep("notify_customer_sms", notify_sms),
    ])


def db_now(conn):
    row = conn.execute("SELECT datetime('now') AS now").fetchone()
    return row["now"] if row else _now()
