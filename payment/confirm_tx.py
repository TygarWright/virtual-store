"""
Transactional wrapper for order payment confirmation.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helpers import url_for
from .transactional import with_transaction

@with_transaction
def confirm_order_payment_tx(conn, order, order_items, *, payment_mode="gateway", razorpay_payment_id=None, razorpay_signature=None):
    """
    Transactional wrapper for _confirm_order_payment.
    
    This function ensures that the entire order confirmation process is atomic:
    - Payment capture
    - Coupon usage increment
    - Entitlement issuance
    - Download token generation
    - Inventory deduction
    - Order status update
    - Notifications
    
    Args:
        conn: Database connection (provided by transactional decorator)
        order: Order object
        order_items: List of order items
        payment_mode: Payment mode ("gateway" or "test")
        razorpay_payment_id: Razorpay payment ID (if applicable)
        razorpay_signature: Razorpay signature (if applicable)
        
    Returns:
        Auto-delivery message if generated, None otherwise
    """
    # Import the original function locally to avoid circular imports
    from app import _confirm_order_payment
    return _confirm_order_payment(conn, order, order_items, payment_mode=payment_mode, 
                                 razorpay_payment_id=razorpay_payment_id, 
                                 razorpay_signature=razorpay_signature)