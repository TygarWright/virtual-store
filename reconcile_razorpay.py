"""Reconcile local orders/refunds against Razorpay provider truth.

Safe to run repeatedly. It detects missing payments, amount mismatches and
provider/local refund discrepancies. It never silently marks a mismatched
transaction successful.
"""
import os, sys, logging
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import database as db
import razorpay_client as rzp
from governance_service import open_exception

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger=logging.getLogger("reconcile_razorpay")

def reconcile():
    if not rzp.is_configured():
        logger.warning("Razorpay not configured — skipping reconciliation.")
        return
    conn=db.get_db(); cutoff=(datetime.now(timezone.utc)-timedelta(minutes=30)).isoformat()
    orders=conn.execute("SELECT * FROM orders WHERE created_at <= ? AND razorpay_order_id IS NOT NULL AND razorpay_order_id != '' ORDER BY created_at ASC", (cutoff,)).fetchall()
    fixed=0; mismatches=0
    for order in orders:
        try:
            payments=rzp.fetch_order_payments(order["razorpay_order_id"])
        except Exception as exc:
            open_exception(conn, code="provider_reconciliation_error", severity="high", title="Razorpay reconciliation failed", description=str(exc), entity="order", entity_id=int(order["id"]))
            continue
        captured=[p for p in payments if p.get("status")=="captured" and p.get("captured") is True]
        total_captured=sum(int(p.get("amount") or 0) for p in captured)
        expected_paise=int(order["amount"] or 0)*100
        if captured and total_captured != expected_paise:
            mismatches += 1
            open_exception(conn, code="payment_amount_mismatch", severity="critical", title="Razorpay payment amount mismatch", description=f"Local ₹{order['amount']} vs provider paise {total_captured}", entity="order", entity_id=int(order["id"]), metadata={"expected_paise":expected_paise,"provider_paise":total_captured})
            continue
        if captured and not order["razorpay_payment_id"]:
            p=captured[0]
            conn.execute("UPDATE orders SET status='paid', payment_state='paid', order_state='paid', razorpay_payment_id=?, paid_at=? WHERE id=? AND status NOT IN ('cancelled','refunded')", (p.get("id"), db.now(), int(order["id"])))
            fixed += 1
        payment_id=order["razorpay_payment_id"] or (captured[0].get("id") if captured else None)
        if payment_id:
            try:
                provider_refunds=rzp.fetch_payment_refunds(payment_id)
                provider_ids={r.get("id") for r in provider_refunds if r.get("id")}
                local=conn.execute("SELECT id, provider_refund_id, status FROM order_refunds WHERE order_id=?", (int(order["id"]),)).fetchall()
                local_ids={r["provider_refund_id"] for r in local if r["provider_refund_id"]}
                missing=provider_ids-local_ids
                if missing:
                    mismatches += len(missing)
                    open_exception(conn, code="refund_reconciliation_mismatch", severity="high", title="Provider refund not recorded locally", description=f"Razorpay refunds missing locally: {sorted(missing)}", entity="order", entity_id=int(order["id"]), metadata={"provider_refund_ids":sorted(missing)})
            except Exception as exc:
                open_exception(conn, code="refund_reconciliation_error", severity="medium", title="Refund reconciliation failed", description=str(exc), entity="order", entity_id=int(order["id"]))
    conn.commit(); conn.close()
    result = {"repaired": fixed, "mismatches": mismatches, "scanned": len(orders)}
    logger.info("Reconciliation complete: %s", result)
    return result

if __name__ == "__main__": main()


def main():
    reconcile()
