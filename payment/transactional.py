"""
Transactional wrappers for critical payment and order operations.
Ensures atomicity of business-critical operations.
"""
from contextlib import contextmanager
from typing import Generator, Optional, Callable, Any
import logging
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database as db

logger = logging.getLogger(__name__)


@contextmanager
def transactional_connection():
    """
    Context manager that provides a transactional database connection.
    
    Usage:
        with transactional_connection() as conn:
            # Do database operations
            # Automatically commits on success, rolls back on exception
            pass
    """
    conn = db.get_db()
    try:
        yield conn
        conn.commit()
        logger.debug("Database transaction committed successfully")
    except Exception as e:
        logger.error(f"Database transaction failed, rolling back: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def with_transaction(func):
    """
    Decorator that wraps a function in a database transaction.
    
    The function must accept a database connection as its first parameter.
    
    Usage:
        @with_transaction
        def my_operation(conn, arg1, arg2):
            # Do database work
            return result
    """
    def wrapper(*args, **kwargs):
        with transactional_connection() as conn:
            return func(conn, *args, **kwargs)
    return wrapper


def record_webhook_event_tx(conn, event_id: str, event_type: str, payload: dict, 
                           provider: str = "razorpay", signature: str = "") -> bool:
    """
    Transactional version of record_webhook_event.
    
    Args:
        conn: Database connection (must be within transaction)
        event_id: Provider event ID
        event_type: Type of event
        payload: Event payload
        provider: Payment provider name
        signature: Webhook signature
        
    Returns:
        True if this was a new event, False if duplicate
    """
    from phase2_services import record_webhook_event
    return record_webhook_event(conn, event_id, event_type, payload, 
                               provider=provider, signature=signature)


def mark_webhook_event_processed_tx(conn, event_id: str, status: str = "processed", 
                                   error: Optional[str] = None, 
                                   provider: str = "razorpay") -> None:
    """
    Transactional version of mark_webhook_event_processed.
    
    Args:
        conn: Database connection (must be within transaction)
        event_id: Provider event ID
        status: Processing status
        error: Error message if any
        provider: Payment provider name
    """
    from phase2_services import mark_webhook_event_processed
    mark_webhook_event_processed(conn, event_id, status=status, error=error, provider=provider)


def confirm_order_payment_tx(conn, order_id: int, 
                            payment_mode: str = "gateway",
                            razorpay_payment_id: Optional[str] = None,
                            razorpay_signature: Optional[str] = None) -> Optional[str]:
    """
    Transactional version of order payment confirmation.
    
    This function encapsulates the entire order confirmation process:
    1. Mark payment as captured
    2. Update coupon usage
    3. Issue entitlements
    4. Generate download tokens
    5. Handle auto-delivery
    6. Update order status
    7. Deduct inventory
    
    Args:
        conn: Database connection (must be within transaction)
        order_id: Order ID to confirm
        payment_mode: Payment mode ("gateway" or "test")
        razorpay_payment_id: Razorpay payment ID (if applicable)
        razorpay_signature: Razorpay signature (if applicable)
        
    Returns:
        Auto-delivery message if generated, None otherwise
    """
    from phase2_services import mark_payment_captured, issue_entitlement, enqueue_email_or_send
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    import helpers
    # Import helper functions individually to avoid circular imports
    from helpers import (
        get_settings, get_csrf_token, check_csrf, check_csrf_api, slugify,
        save_product_image, delete_file_quietly, send_email, email_enabled,
        rate_limited, turnstile_enabled, verify_turnstile,
        firebase_auth_enabled, verify_firebase_id_token, prewarm_firebase_certs,
        generate_otp_code, store_otp, verify_otp_code,
        notify_admins_new_order, webpush_notify_admins_new_order,
        whatsapp_enabled, send_whatsapp, twilio_enabled, send_sms,
        allowed_product_file, save_product_file, product_file_path,
        generate_download_tokens, migrate_legacy_product_files,
        customer_login_required,
        track_cart_add, track_cart_contact,
        has_permission,
        generate_download_tokens,
        notify_admins_new_order,
        webpush_notify_admins_new_order,
        customer_email_notifications_enabled,
        logger,
        get_setting,
        url_for
    )
    import json as _json
    
    # Get order and order items
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        logger.warning(f"Order {order_id} not found")
        return None
        
    order_items = conn.execute(
        "SELECT * FROM order_items WHERE order_id = ?", (order_id,)
    ).fetchall()
    
    current_status = (order["status"] or "").lower()
    if current_status in {"paid", "delivered"}:
        logger.info(f"Order {order_id} already paid/delivered, skipping confirmation")
        return order["delivery_message"] if order["delivery_message"] else None

    # Capture the payment and move the order state exactly once before any
    # coupon, stock, entitlement, token, or notification side effects.
    if not mark_payment_captured(conn, order["id"]):
        logger.warning(f"Failed to mark payment as captured for order {order_id}")
        return None

    coupon_code = (order["coupon_code"] or "").strip().upper()
    if coupon_code:
        try:
            # Atomically increment used_count only on confirmed payment
            conn.execute(
                "UPDATE coupons SET used_count = used_count + 1 WHERE code = ? AND (usage_limit IS NULL OR used_count < usage_limit)",
                (coupon_code,),
            )
            coupon = conn.execute("SELECT id FROM coupons WHERE code = ?", (coupon_code,)).fetchone()
            if coupon:
                conn.execute(
                    "INSERT INTO coupon_usage (coupon_id, order_id, customer_email, discount_amount, used_at) VALUES (?, ?, ?, ?, ?)",
                    (coupon["id"], order["id"], order["customer_email"], order["discount_amount"], db.now()),
                )
        except Exception:
            logger.exception("Coupon usage recording failed for order %s", order["order_ref"])

    # Issue protected links as part of the confirmed-payment transaction. The
    # helper is idempotent, so webhook/client retries cannot create duplicate
    # tokens. Keep manual-delivery orders paid; admins can still paste these
    # protected links into the delivery message when they deliver the order.
    product_ids = [it["product_id"] for it in (order_items or []) if it["product_id"]]
    if not product_ids and order["product_id"]:
        product_ids = [order["product_id"]]
    for product_id in sorted(set(product_ids)):
        issue_entitlement(
            conn,
            order["id"],
            product_id,
            customer_id=order["customer_id"] if "customer_id" in order.keys() else None,
        )
    file_tokens = generate_download_tokens(conn, order["id"], product_ids)
    token_lines = [
        f"���📎 {item['filename']}: {url_for('download_product', token=item['token'], _external=True)}"
        for item in file_tokens
    ]

    auto_message = None
    auto_deliver_enabled = str(get_settings().get("auto_deliver_enabled", "true")).lower() != "false"
    if auto_deliver_enabled:
        auto_message = _maybe_auto_deliver(conn, order, order_items)
        # Only automatic-delivery products are marked delivered here. Manual
        # orders still receive generated tokens, exposed to the admin detail
        # page for later inclusion when the admin completes delivery.
        if auto_message is not None and token_lines:
            auto_message = (auto_message + "\n\n" if auto_message else "") + "\n".join(token_lines)
            conn.execute(
                "UPDATE orders SET delivery_message = ? WHERE id = ?",
                (auto_message, order["id"]),
            )

    paid_at = db.now()
    if auto_message is None:
        conn.execute(
            "UPDATE orders SET status = 'paid', paid_at = ?, payment_mode = ?, razorpay_payment_id = COALESCE(?, razorpay_payment_id), razorpay_signature = COALESCE(?, razorpay_signature) WHERE id = ?",
            (paid_at, payment_mode, razorpay_payment_id, razorpay_signature, order["id"]),
        )
    else:
        conn.execute(
            "UPDATE orders SET status = 'delivered', paid_at = COALESCE(paid_at, ?), payment_mode = ?, razorpay_payment_id = COALESCE(?, razorpay_payment_id), razorpay_signature = COALESCE(?, razorpay_signature) WHERE id = ?",
            (paid_at, payment_mode, razorpay_payment_id, razorpay_signature, order["id"]),
        )
        # Import here to avoid circular dependency
        from phase2_services import transition_order_state
        transition_order_state(conn, order["id"], "delivered", expected_states={"paid"})

    # Deduct stock for each item — atomic with built-in oversell guard
    for item in (order_items or []):
        if item["product_id"]:
            conn.execute(
                "UPDATE products SET quantity = quantity - ? WHERE id = ? AND quantity >= ?",
                (item["quantity"], item["product_id"], item["quantity"]),
            )

    # Note: Commit is handled by the transactional context manager

    try:
        notify_admins_new_order(order["id"])
        webpush_notify_admins_new_order(order["id"])
    except Exception:
        pass

    if customer_email_notifications_enabled():
        try:
            item_line = order["product_name"] if not order_items else ", ".join(
                f"{it['product_name']} x{it['quantity']}" for it in order_items
            )
            subject = f"Your order {order['order_ref']} is confirmed"
            body = (
                f"Hi {order['customer_name']},\n\n"
                f"We have received your order for \"{item_line}\".\n\n"
            )
            if auto_message:
                subject = f"Your order {order['order_ref']} has been delivered"
                body += f"Your download/details are ready below:\n\n{auto_message}\n\n"
            body += f"Order reference: {order['order_ref']}\n\nThank you for shopping with us."
            enqueue_email_or_send(
                conn,
                to=order["customer_email"],
                subject=subject,
                body=body,
                idempotency_key=f"order-email:{order["id"]}:{"delivered" if auto_message else "confirmed"}",
                logger=current_app.logger if "current_app" in globals() else None,
            )
        except Exception:
            pass

    return auto_message


def _maybe_auto_deliver(conn, order, order_items):
    """If every product in this order has delivery_mode='automatic', marks
    the order delivered right away and returns the combined delivery
    message. Otherwise leaves the order at 'paid' for manual review and
    returns None. Must be called before conn.commit()/conn.close()."""
    from helpers import url_for
    if order_items:
        product_ids = [it["product_id"] for it in order_items if it["product_id"]]
        if len(product_ids) != len(order_items):
            return None  # a purchased product was later deleted — play it safe
        placeholders = ",".join("?" * len(product_ids))
        rows = conn.execute(
            f"SELECT id, name, delivery_mode, auto_delivery_content FROM products "
            f"WHERE id IN ({placeholders})",
            product_ids,
        ).fetchall()
        by_id = {r["id"]: r for r in rows}
        if len(by_id) != len(set(product_ids)):
            return None  # a purchased product was later deleted — play it safe
        if not all(by_id[pid]["delivery_mode"] == "automatic" for pid in product_ids):
            return None
        parts = [
            f"{by_id[it['product_id']]['name']}:\\n{(by_id[it['product_id']]['auto_delivery_content'] or '').strip()}"
            for it in order_items
            if (by_id[it['product_id']]['auto_delivery_content'] or '').strip()
        ]
        message = "\n\n".join(parts).strip()
    else:
        if not order["product_id"]:
            return None
        product = conn.execute(
            "SELECT delivery_mode, auto_delivery_content FROM products WHERE id = ?",
            (order["product_id"],),
        ).fetchone()
        if not product or product["delivery_mode"] != "automatic":
            return None
        message = (product["auto_delivery_content"] or "").strip()

    conn.execute(
        "UPDATE orders SET status = 'delivered', delivery_message = ?, "
        "delivered_at = ?, auto_delivered = 1 WHERE id = ?",
        (message, db.now(), order["id"]),
    )
    return message


# Export the main transactional functions
__all__ = [
    "transactional_connection",
    "with_transaction",
    "record_webhook_event_tx",
    "mark_webhook_event_processed_tx",
    "confirm_order_payment_tx",
]