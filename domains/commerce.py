"""Domain-level validation helpers independent of HTTP/Flask."""
from typing import Mapping


def validate_amount(amount: int) -> int:
    value = int(amount)
    if value <= 0:
        raise ValueError("amount must be greater than zero")
    return value


def validate_currency(currency: str) -> str:
    value = str(currency or "").upper().strip()
    if len(value) != 3 or not value.isalpha():
        raise ValueError("currency must be a valid ISO-4217 code")
    return value


def validate_order_identity(order: Mapping[str, object], *, payment_id: str) -> bool:
    return bool(order and payment_id and order.get("razorpay_payment_id") in (None, "", payment_id))
