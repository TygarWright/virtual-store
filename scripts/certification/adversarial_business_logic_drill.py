#!/usr/bin/env python3
"""Offline adversarial business-logic checks using TITAN's canonical primitives."""
from __future__ import annotations
from pathlib import Path
import importlib.util
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    inv = load('titan_invariants', 'titan_invariants.py')
    # Money cannot be negative.
    try:
        inv.assert_non_negative_amount(-1, field='amount')
    except (AssertionError, ValueError):
        pass
    else:
        raise AssertionError('negative money accepted')

    # Refund cannot exceed paid amount.
    refund = getattr(inv, 'assert_refund_within_paid', None)
    if refund:
        try:
            refund({'amount': 100}, 101)
        except (AssertionError, ValueError):
            pass
        else:
            raise AssertionError('over-refund accepted')

    # Protected margin cannot be violated.
    margin = getattr(inv, 'assert_min_margin', None)
    if margin:
        try:
            margin(sale_price=100, cost_price=90, discount_amount=20, min_margin_percent=5)
        except (AssertionError, ValueError):
            pass
        else:
            raise AssertionError('margin floor bypass accepted')

    # Canonical event contract must reject aggregate/topic mismatch.
    backend = load('backend_kernel', 'backend_kernel.py')
    if hasattr(backend, 'validate_business_event'):
        try:
            backend.validate_business_event(topic='order.paid', aggregate='refund', payload={})
        except (AssertionError, ValueError):
            pass
        else:
            raise AssertionError('event aggregate bypass accepted')

    # Approval self-approval: source contract must contain an explicit requester/approver guard.
    gov_src = (ROOT / 'governance_service.py').read_text(encoding='utf-8')
    assert 'requested_by' in gov_src and 'approved_by' in gov_src, 'approval identity fields missing'
    assert 'int(row["requested_by"]) == int(approved_by)' in gov_src, 'self-approval guard missing'

    print('ADVERSARIAL_BUSINESS_LOGIC_DRILL: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
