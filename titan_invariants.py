"""Business-physics invariants for TITAN.

These are intentionally small, deterministic predicates that can be used at
mutation boundaries and in tests. They do not replace workflows; they make
business rules explicit and testable.
"""
from __future__ import annotations

from typing import Any, Mapping


class InvariantViolation(ValueError):
    """Raised when a non-negotiable business invariant is violated."""



def assert_positive_amount(amount: Any, *, field: str = "amount") -> int:
    value = int(amount)
    if value <= 0:
        raise InvariantViolation(f"{field} must be greater than zero")
    return value


def assert_non_negative_amount(amount: Any, *, field: str = "amount") -> int:
    value = int(amount)
    if value < 0:
        raise InvariantViolation(f"{field} cannot be negative")
    return value


def assert_refund_within_paid(order: Mapping[str, Any], refund_amount: Any) -> int:
    requested = assert_positive_amount(refund_amount, field="refund_amount")
    order_amount = assert_non_negative_amount(order.get("amount", 0), field="order.amount")
    already_refunded = assert_non_negative_amount(order.get("refunded_amount", 0) or 0, field="order.refunded_amount")
    if requested + already_refunded > order_amount:
        raise InvariantViolation("refund would exceed refundable amount")
    return requested


def assert_margin_floor(*, sale_price: Any, cost_price: Any, min_margin_percent: Any,
                        discount_amount: Any = 0) -> int:
    sale = assert_non_negative_amount(sale_price, field="sale_price")
    cost = assert_non_negative_amount(cost_price, field="cost_price")
    discount = assert_non_negative_amount(discount_amount, field="discount_amount")
    final_price = sale - discount
    if final_price < 0:
        raise InvariantViolation("discount cannot make final price negative")
    margin_floor = int(min_margin_percent)
    if margin_floor < 0 or margin_floor >= 100:
        raise InvariantViolation("min_margin_percent must be between 0 and 99")
    if final_price < cost:
        raise InvariantViolation("promotion would sell below cost")
    if final_price * 100 < cost * (100 + margin_floor):
        raise InvariantViolation("promotion would violate the protected margin floor")
    return final_price


def assert_stock_non_negative(quantity: Any, *, field: str = "quantity") -> int:
    value = assert_non_negative_amount(quantity, field=field)
    return value


def assert_transition_allowed(current: str, new: str, allowed: Mapping[str, set[str]]) -> None:
    if new not in allowed.get(current, set()):
        raise InvariantViolation(f"invalid state transition: {current} -> {new}")
