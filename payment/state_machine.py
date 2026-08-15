"""
Order and payment state machine implementation.
Provides explicit state transitions with guards to prevent invalid state changes.
"""
from enum import Enum
from typing import Optional, Set, Dict, Any
import logging

logger = logging.getLogger(__name__)


def _get_database():
    """Load the application database module without importing config eagerly."""
    try:
        import database as db
    except ImportError:  # pragma: no cover - supports package-based imports
        from .. import database as db
    return db


class PaymentState(Enum):
    """Payment states following provider and order-lifecycle conventions."""
    PENDING = "pending"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    DECLINED = "declined"
    EXPIRED = "expired"
    VOIDED = "voided"
    REFUND_PENDING = "refund_pending"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    DISPUTED = "disputed"
    CHARGEBACK = "chargeback"


class OrderState(Enum):
    """Order states for the virtual store."""
    CREATED = "created"
    PAID = "paid"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


# Define allowed transitions. Every state is present, including terminal states,
# so callers cannot accidentally bypass the state machine with an implicit default.
PAYMENT_TRANSITIONS: Dict[PaymentState, Set[PaymentState]] = {
    PaymentState.PENDING: {
        PaymentState.AUTHORIZED,
        PaymentState.CAPTURED,
        PaymentState.FAILED,
        PaymentState.DECLINED,
        PaymentState.EXPIRED,
        PaymentState.VOIDED,
    },
    PaymentState.AUTHORIZED: {
        PaymentState.CAPTURED,
        PaymentState.FAILED,
        PaymentState.DECLINED,
        PaymentState.EXPIRED,
        PaymentState.VOIDED,
    },
    PaymentState.CAPTURED: {
        PaymentState.REFUND_PENDING,
        PaymentState.PARTIALLY_REFUNDED,
        PaymentState.REFUNDED,
        PaymentState.DISPUTED,
        PaymentState.CHARGEBACK,
    },
    PaymentState.FAILED: {
        PaymentState.PENDING,  # A failed attempt may be retried.
    },
    PaymentState.DECLINED: {
        PaymentState.PENDING,  # A declined attempt may be retried.
    },
    PaymentState.EXPIRED: {
        PaymentState.PENDING,  # A new payment attempt may be created.
    },
    PaymentState.VOIDED: set(),  # Terminal state.
    PaymentState.REFUND_PENDING: {
        PaymentState.PARTIALLY_REFUNDED,
        PaymentState.REFUNDED,
    },
    PaymentState.PARTIALLY_REFUNDED: {
        PaymentState.REFUND_PENDING,
        PaymentState.REFUNDED,
        PaymentState.DISPUTED,
        PaymentState.CHARGEBACK,
    },
    PaymentState.DISPUTED: {
        PaymentState.CAPTURED,    # Dispute resolved in the merchant's favor.
        PaymentState.CHARGEBACK,  # Dispute resolved against the merchant.
    },
    PaymentState.CHARGEBACK: set(),  # Terminal state.
    PaymentState.REFUNDED: set(),  # Terminal state.
}

ORDER_TRANSITIONS: Dict[OrderState, Set[OrderState]] = {
    OrderState.CREATED: {
        OrderState.PAID,
        OrderState.CANCELLED
    },
    OrderState.PAID: {
        OrderState.DELIVERED,
        OrderState.CANCELLED,
        OrderState.REFUNDED
    },
    OrderState.DELIVERED: {
        OrderState.CANCELLED,
        OrderState.REFUNDED
    },
    OrderState.CANCELLED: set(),  # Terminal state
    OrderState.REFUNDED: set()    # Terminal state
}


def can_transition_payment(current_state: PaymentState,
                          new_state: PaymentState) -> bool:
    """
    Check if a payment state transition is allowed.
    
    Args:
        current_state: Current payment state
        new_state: Desired new payment state
        
    Returns:
        True if transition is allowed
    """
    if not isinstance(current_state, PaymentState) or not isinstance(new_state, PaymentState):
        logger.warning(f"Unknown payment state transition: {current_state} -> {new_state}")
        return False

    if current_state not in PAYMENT_TRANSITIONS:
        logger.warning(f"Unknown payment state: {current_state}")
        return False

    return new_state in PAYMENT_TRANSITIONS[current_state]


def can_transition_order(current_state: OrderState, 
                        new_state: OrderState) -> bool:
    """
    Check if an order state transition is allowed.
    
    Args:
        current_state: Current order state
        new_state: Desired new order state
        
    Returns:
        True if transition is allowed
    """
    if not isinstance(current_state, OrderState) or not isinstance(new_state, OrderState):
        logger.warning(f"Unknown order state transition: {current_state} -> {new_state}")
        return False

    if current_state not in ORDER_TRANSITIONS:
        logger.warning(f"Unknown order state: {current_state}")
        return False

    return new_state in ORDER_TRANSITIONS[current_state]


def transition_payment_state_safe(conn, order_id: int, new_state: PaymentState,
                                 expected_states: Optional[Set[PaymentState]] = None) -> bool:
    """
    Safely transition payment state with validation.
    
    Args:
        conn: Database connection
        order_id: Order ID
        new_state: Desired new payment state
        expected_states: Set of acceptable current states (if None, any state allowed)
        
    Returns:
        True if transition was successful
    """
    db = _get_database()

    # Read the state only to validate the requested transition. The UPDATE below
    # repeats the current state in its predicate so a concurrent writer cannot
    # overwrite a newer payment state.
    row = conn.execute(
        "SELECT payment_state FROM orders WHERE id = ?",
        (order_id,)
    ).fetchone()

    if not row:
        logger.warning(f"Order {order_id} not found")
        return False

    current_state_str = row["payment_state"] or PaymentState.PENDING.value
    try:
        current_state = PaymentState(current_state_str)
    except (TypeError, ValueError):
        logger.warning(f"Invalid payment state in database: {current_state_str}")
        return False

    # Check the transition and optional caller-supplied optimistic guard before
    # touching the database.
    if not can_transition_payment(current_state, new_state):
        logger.warning(
            f"Invalid payment transition: {current_state} -> {new_state} "
            f"for order {order_id}"
        )
        return False

    if expected_states is not None and current_state not in set(expected_states):
        logger.warning(
            f"Payment state {current_state} not in expected states {expected_states} "
            f"for order {order_id}"
        )
        return False

    try:
        updated = conn.execute(
            """UPDATE orders
               SET payment_state = ?
               WHERE id = ? AND payment_state = ?""",
            (new_state.value, order_id, current_state.value),
        )
        if updated.rowcount != 1:
            logger.info(
                f"Payment state transition lost race or order disappeared: "
                f"{current_state} -> {new_state} for order {order_id}"
            )
            return False
        conn.commit()
        logger.info(
            f"Payment state transitioned: {current_state} -> {new_state} "
            f"for order {order_id}"
        )
        return True
    except Exception as e:
        logger.error(f"Failed to update payment state for order {order_id}: {e}")
        conn.rollback()
        return False


def transition_order_state_safe(conn, order_id: int, new_state: OrderState,
                               expected_states: Optional[Set[OrderState]] = None) -> bool:
    """
    Safely transition order state with validation.
    
    Args:
        conn: Database connection
        order_id: Order ID
        new_state: Desired new order state
        expected_states: Set of acceptable current states (if None, any state allowed)
        
    Returns:
        True if transition was successful
    """
    db = _get_database()

    # Get current state
    row = conn.execute(
        "SELECT status FROM orders WHERE id = ?",
        (order_id,)
    ).fetchone()

    if not row:
        logger.warning(f"Order {order_id} not found")
        return False
        
    current_state_str = row["status"] or "created"
    try:
        current_state = OrderState(current_state_str)
    except ValueError:
        logger.warning(f"Invalid order status in database: {current_state_str}")
        return False
        
    # Check if transition is allowed
    if not can_transition_order(current_state, new_state):
        logger.warning(
            f"Invalid order transition: {current_state} -> {new_state} "
            f"for order {order_id}"
        )
        return False
        
    # Check expected states if provided
    if expected_states is not None and current_state not in expected_states:
        logger.warning(
            f"Order state {current_state} not in expected states {expected_states} "
            f"for order {order_id}"
        )
        return False
        
    # Perform the transition with an optimistic concurrency guard.
    try:
        updated = conn.execute(
            """UPDATE orders
               SET status = ?
               WHERE id = ? AND status = ?""",
            (new_state.value, order_id, current_state.value),
        )
        if updated.rowcount != 1:
            logger.info(
                f"Order state transition lost race or order disappeared: "
                f"{current_state} -> {new_state} for order {order_id}"
            )
            return False
        conn.commit()
        logger.info(
            f"Order state transitioned: {current_state} -> {new_state} "
            f"for order {order_id}"
        )
        return True
    except Exception as e:
        logger.error(f"Failed to update order state for order {order_id}: {e}")
        conn.rollback()
        return False


def get_payment_state(order_id: int) -> Optional[PaymentState]:
    """
    Get current payment state for an order.
    
    Args:
        order_id: Order ID
        
    Returns:
        PaymentState or None if order not found
    """
    db = _get_database()

    conn = db.get_db()
    try:
        row = conn.execute(
            "SELECT payment_state FROM orders WHERE id = ?",
            (order_id,)
        ).fetchone()

        if not row:
            return None
            
        state_str = row["payment_state"] or PaymentState.PENDING.value
        return PaymentState(state_str)
    finally:
        conn.close()


def get_order_state(order_id: int) -> Optional[OrderState]:
    """
    Get current order state for an order.
    
    Args:
        order_id: Order ID
        
    Returns:
        OrderState or None if order not found
    """
    db = _get_database()
    
    conn = db.get_db()
    try:
        row = conn.execute(
            "SELECT status FROM orders WHERE id = ?", 
            (order_id,)
        ).fetchone()
        
        if not row:
            return None
            
        state_str = row["status"] or "created"
        return OrderState(state_str)
    finally:
        conn.close()