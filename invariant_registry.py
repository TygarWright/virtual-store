"""Canonical TITAN business-physics registry.

Each invariant is named, described, executable, and covered by a deterministic
smoke test. The registry is intentionally small and dependency-free so it can
run in CI, deployment checks, and operator diagnostics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any

from titan_invariants import (
    assert_positive_amount,
    assert_non_negative_amount,
    assert_refund_within_paid,
    assert_margin_floor,
    assert_stock_non_negative,
    assert_transition_allowed,
    InvariantViolation,
)

@dataclass(frozen=True)
class InvariantSpec:
    key: str
    title: str
    description: str
    validator: Callable[[], Any]


def _expect_violation(fn: Callable[[], Any]) -> None:
    try:
        fn()
    except InvariantViolation:
        return
    raise AssertionError("expected InvariantViolation")


def registry() -> tuple[InvariantSpec, ...]:
    return (
        InvariantSpec("money.positive", "Positive money", "Financial amounts that represent an action must be > 0.", lambda: assert_positive_amount(1)),
        InvariantSpec("money.non_negative", "Non-negative money", "Stored/derived monetary quantities cannot be negative.", lambda: assert_non_negative_amount(0)),
        InvariantSpec("refund.within_paid", "Refund cannot exceed paid", "A refund plus prior refunds cannot exceed the order amount.", lambda: _expect_violation(lambda: assert_refund_within_paid({"amount": 100, "refunded_amount": 90}, 20))),
        InvariantSpec("promotion.margin", "Protected margin", "A promotion cannot cross the configured minimum margin floor.", lambda: _expect_violation(lambda: assert_margin_floor(sale_price=100, cost_price=90, min_margin_percent=15, discount_amount=10))),
        InvariantSpec("inventory.non_negative", "Inventory cannot go negative", "Sellable quantity cannot be below zero.", lambda: _expect_violation(lambda: assert_stock_non_negative(-1))),
        InvariantSpec("state.transition", "Explicit state transitions", "Critical state machines may only move through declared transitions.", lambda: _expect_violation(lambda: assert_transition_allowed("paid", "created", {"paid": {"refunded"}}))),
    )


def verify_all() -> list[dict[str, Any]]:
    results = []
    for spec in registry():
        try:
            spec.validator()
            results.append({"key": spec.key, "status": "PASS", "title": spec.title})
        except Exception as exc:
            results.append({"key": spec.key, "status": "FAIL", "title": spec.title, "error": str(exc)[:300]})
    return results


__all__ = ["InvariantSpec", "registry", "verify_all"]
