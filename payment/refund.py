"""
Refund workflow implementation.
Handles the complete refund process from initiation to completion.
"""
from typing import Optional, Dict, Any
import logging
import sys
import os
import sqlite3
import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database as db
from payment.gateways import get_payment_gateway
from titan_invariants import assert_refund_within_paid

logger = logging.getLogger(__name__)


def initiate_refund(conn, order_id: int, amount: Optional[int] = None,
                   reason: str = "") -> Dict[str, Any]:
    """Create a local refund intent. No money is moved by this function."""
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        return {"success": False, "error": "Order not found"}
    if order["status"] not in ("paid", "delivered"):
        return {"success": False, "error": f"Order cannot be refunded in status: {order['status']}"}

    payment = conn.execute(
        "SELECT * FROM order_payments WHERE order_id = ? AND status = 'captured' ORDER BY id DESC LIMIT 1",
        (order_id,),
    ).fetchone()
    payment_id = (payment["provider_payment_id"] if payment else None) or order["razorpay_payment_id"]
    if not payment_id:
        return {"success": False, "error": "No captured provider payment found for order"}

    refund_amount = assert_refund_within_paid(order, int(amount if amount is not None else order["amount"]))
    if refund_amount <= 0:
        return {"success": False, "error": "Refund amount must be positive"}

    already_refunded = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM order_refunds WHERE order_id = ? AND status = 'processed'",
        (order_id,),
    ).fetchone()["total"]
    if refund_amount + int(already_refunded or 0) > int(order["amount"]):
        return {"success": False, "error": "Refund amount exceeds the remaining refundable amount"}

    existing = conn.execute(
        "SELECT id, status FROM order_refunds WHERE order_id = ? AND amount = ? AND status IN ('pending', 'processing') ORDER BY id DESC LIMIT 1",
        (order_id, refund_amount),
    ).fetchone()
    if existing:
        return {"success": True, "refund_id": existing["id"], "amount": refund_amount, "currency": "INR", "status": existing["status"]}

    try:
        conn.execute(
            "INSERT INTO order_refunds (order_id, amount, reason, status, initiated_at) VALUES (?, ?, ?, 'pending', ?)",
            (order_id, refund_amount, reason, db.now()),
        )
        refund_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        try:
            from backend_kernel import publish_business_event
            publish_business_event(conn, topic="refund.initiated", aggregate="refund", aggregate_id=int(refund_id),
                                   payload={"refund_id": int(refund_id), "order_id": int(order_id), "amount": int(refund_amount), "reason": reason[:1000]})
        except Exception:
            pass
        conn.commit()
    except sqlite3.IntegrityError:
        # A concurrent request may have created the same open refund between
        # our read and insert. The partial unique index makes that race safe.
        conn.rollback()
        existing = conn.execute(
            "SELECT id, status FROM order_refunds WHERE order_id = ? AND amount = ? AND status IN ('pending', 'processing') ORDER BY id DESC LIMIT 1",
            (order_id, refund_amount),
        ).fetchone()
        if not existing:
            raise
        refund_id = existing["id"]
        return {
            "success": True,
            "refund_id": refund_id,
            "amount": refund_amount,
            "currency": "INR",
            "status": existing["status"],
            "provider_payment_id": payment_id,
        }
    return {
        "success": True,
        "refund_id": refund_id,
        "amount": refund_amount,
        "currency": "INR",
        "status": "pending",
        "provider_payment_id": payment_id,
    }


def _process_refund_impl(conn, refund_id: int, provider_refund_id: Optional[str] = None) -> Dict[str, Any]:
    """Execute/refetch a refund through the configured provider safely."""
    refund = conn.execute("SELECT * FROM order_refunds WHERE id = ?", (refund_id,)).fetchone()
    if not refund:
        return {"success": False, "error": "Refund not found"}
    if refund["status"] == "processed":
        return {"success": True, "refund_id": refund_id, "status": "processed", "provider_refund_id": refund["provider_refund_id"]}
    if refund["status"] == "failed":
        return {"success": False, "refund_id": refund_id, "status": "failed", "error": refund["failure_reason"] or "Refund previously failed"}

    order = conn.execute("SELECT * FROM orders WHERE id = ?", (refund["order_id"],)).fetchone()
    if not order:
        return {"success": False, "error": "Associated order not found"}

    payment = conn.execute(
        "SELECT * FROM order_payments WHERE order_id = ? AND status = 'captured' ORDER BY id DESC LIMIT 1",
        (order["id"],),
    ).fetchone()
    payment_id = (payment["provider_payment_id"] if payment else None) or order["razorpay_payment_id"]
    provider = ((payment["provider"] if payment else None) or "razorpay").lower()
    if not payment_id:
        return {"success": False, "error": "No captured provider payment found"}

    conn.execute("UPDATE order_refunds SET status = 'processing' WHERE id = ? AND status = 'pending'", (refund_id,))
    conn.commit()

    gateway = get_payment_gateway(provider, config={})
    # Virtual Store stores order/refund amounts in rupees; gateways use minor units.
    result = gateway.refund_payment(
        payment_id,
        amount=int(refund["amount"]) * 100,
        idempotency_key=f"virtual-store-refund-{refund_id}",
    )
    provider_id = result.provider_refund_id or provider_refund_id

    if not result.success:
        if result.retryable:
            # Never turn an uncertain network outcome into a terminal failure.
            # The same Razorpay idempotency key will be reused on the next retry.
            conn.execute(
                "UPDATE order_refunds SET status='pending', provider_refund_id=? WHERE id=?",
                (provider_id, refund_id),
            )
            conn.commit()
            return {"success": True, "refund_id": refund_id, "status": "pending", "provider_refund_id": provider_id, "error": "Provider response was inconclusive; safe retry is available."}
        conn.execute(
            "UPDATE order_refunds SET status='failed', failure_reason=?, failed_at=?, provider_refund_id=? WHERE id=?",
            (result.error_message or "Provider refund failed", db.now(), provider_id, refund_id),
        )
        try:
            from backend_kernel import publish_business_event
            publish_business_event(conn, topic="refund.failed", aggregate="refund", aggregate_id=int(refund_id),
                                   payload={"refund_id": int(refund_id), "order_id": int(order["id"]), "amount": int(refund["amount"]), "error": str(result.error_message or "Provider refund failed")[:500]})
        except Exception:
            pass
        conn.commit()
        return {"success": False, "refund_id": refund_id, "status": "failed", "error": result.error_message or "Provider refund failed"}

    provider_status = (result.status or "pending").lower()
    if provider_status in {"processed", "refunded"}:
        conn.execute(
            "UPDATE order_refunds SET status='processed', provider_refund_id=?, processed_at=? WHERE id=?",
            (provider_id, db.now(), refund_id),
        )
        total = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS total FROM order_refunds WHERE order_id=? AND status='processed'",
            (order["id"],),
        ).fetchone()["total"]
        if int(total or 0) >= int(order["amount"]):
            conn.execute(
                "UPDATE orders SET status='refunded', payment_state='refunded', refunded_amount=?, refunded_at=?, razorpay_refund_id=? WHERE id=?",
                (int(total), db.now(), provider_id, order["id"]),
            )
        else:
            conn.execute(
                "UPDATE orders SET refunded_amount=?, razorpay_refund_id=? WHERE id=?",
                (int(total), provider_id, order["id"]),
            )
        try:
            from backend_kernel import publish_business_event
            publish_business_event(conn, topic="refund.processed", aggregate="refund", aggregate_id=int(refund_id),
                                   payload={"refund_id": int(refund_id), "order_id": int(order["id"]), "amount": int(refund["amount"]), "provider_refund_id": provider_id})
        except Exception:
            pass
        conn.commit()
        return {"success": True, "refund_id": refund_id, "order_id": order["id"], "status": "processed", "provider_refund_id": provider_id, "amount": refund["amount"]}

    # Razorpay may accept a normal refund while it remains pending. Keep the
    # local refund pending and let refund webhooks finalize it.
    conn.execute(
        "UPDATE order_refunds SET status='pending', provider_refund_id=? WHERE id=?",
        (provider_id, refund_id),
    )
    conn.commit()
    return {"success": True, "refund_id": refund_id, "order_id": order["id"], "status": "pending", "provider_refund_id": provider_id, "amount": refund["amount"]}



# Durable workflow wrapper: the existing refund implementation remains the
# business-state authority; the workflow adds persistence/recovery around it.
def process_refund(conn, refund_id: int, provider_refund_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        from titan_workflows import DurableWorkflow, WorkflowStep
        refund = conn.execute("SELECT * FROM order_refunds WHERE id=?", (int(refund_id),)).fetchone()
        if not refund:
            return {"success": False, "error": "Refund not found"}
        flow = DurableWorkflow(conn, workflow_type="order.refund", aggregate_type="refund", aggregate_id=str(refund_id))
        def execute(_conn, ctx):
            result = _process_refund_impl(_conn, refund_id, provider_refund_id)
            return {"result": result}
        result = flow.run([WorkflowStep("process_refund", execute)])
        if result.get("status") == "completed":
            return dict(result.get("context") or {}).get("result") or _process_refund_impl(conn, refund_id, provider_refund_id)
        if result.get("status") == "waiting":
            return {"success": True, "refund_id": refund_id, "status": "pending", "workflow_id": result.get("workflow_id")}
        return result
    except Exception as exc:
        logger.exception("Durable refund workflow failed")
        # Preserve the historical API surface while the durable workflow state
        # records the failure. Do not retry the provider call implicitly here.
        return {"success": False, "refund_id": refund_id, "status": "failed", "error": str(exc)[:500]}


def get_refund_status(conn, refund_id: int) -> Optional[Dict[str, Any]]:
    """
    Get the status of a refund.
    
    Args:
        conn: Database connection
        refund_id: Refund ID
        
    Returns:
        Dictionary with refund status or None if not found
    """
    refund = conn.execute(
        "SELECT * FROM order_refunds WHERE id = ?", (refund_id,)
    ).fetchone()
    
    if not refund:
        return None
        
    return {
        "id": refund["id"],
        "order_id": refund["order_id"],
        "amount": refund["amount"],
        "reason": refund["reason"],
        "status": refund["status"],
        "provider_refund_id": refund["provider_refund_id"],
        "initiated_at": refund["initiated_at"],
        "processed_at": refund["processed_at"]
    }


def list_order_refunds(conn, order_id: int) -> list:
    """
    List all refunds for an order.
    
    Args:
        conn: Database connection
        order_id: Order ID
        
    Returns:
        List of refund dictionaries
    """
    refunds = conn.execute(
        "SELECT * FROM order_refunds WHERE order_id = ? ORDER BY initiated_at DESC",
        (order_id,)
    ).fetchall()
    
    return [
        {
            "id": r["id"],
            "amount": r["amount"],
            "reason": r["reason"],
            "status": r["status"],
            "provider_refund_id": r["provider_refund_id"],
            "initiated_at": r["initiated_at"],
            "processed_at": r["processed_at"]
        }
        for r in refunds
    ]


# Export the main functions
__all__ = [
    "initiate_refund",
    "process_refund",
    "get_refund_status",
    "list_order_refunds"
]