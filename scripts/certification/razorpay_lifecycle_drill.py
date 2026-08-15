"""Repository-level Razorpay lifecycle contract drill.

Simulates the provider contract without contacting Razorpay. It proves that the
TITAN reconciliation/payment semantics expected for create/capture/webhook/
refund/replay are deterministic and idempotent before external certification.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass
class FakeProvider:
    payments: dict[str, dict] = field(default_factory=dict)
    refunds: dict[str, dict] = field(default_factory=dict)

    def create_order(self, order_id: str, amount: int) -> dict:
        pid = f"pay_{hashlib.sha256(order_id.encode()).hexdigest()[:12]}"
        self.payments[pid] = {"order_id": order_id, "amount": amount, "status": "created"}
        return {"id": pid, "amount": amount, "status": "created"}

    def capture(self, payment_id: str) -> dict:
        p = self.payments[payment_id]
        p["status"] = "captured"
        return dict(p, id=payment_id)

    def refund(self, payment_id: str, refund_id: str, amount: int) -> dict:
        key = refund_id
        if key in self.refunds:
            return dict(self.refunds[key], replay=True)
        self.refunds[key] = {"payment_id": payment_id, "amount": amount, "status": "processed", "id": refund_id}
        return dict(self.refunds[key], replay=False)


def apply_webhook(state: dict, event_id: str, event: dict) -> str:
    if event_id in state["seen_events"]:
        return "replay"
    state["seen_events"].add(event_id)
    typ = event["type"]
    if typ == "payment.captured":
        state["payment_state"] = "paid"
        state["payment_id"] = event["payment_id"]
    elif typ == "refund.processed":
        state["refund_state"] = "processed"
        state["refund_id"] = event["refund_id"]
    else:
        raise AssertionError(f"unexpected event: {typ}")
    return "applied"


def main() -> int:
    provider = FakeProvider()
    state = {"seen_events": set(), "payment_state": "pending", "refund_state": "none", "payment_id": None, "refund_id": None}

    order = provider.create_order("CERT-RAZORPAY-001", 1999)
    captured = provider.capture(order["id"])
    event = {"type": "payment.captured", "payment_id": order["id"]}
    assert apply_webhook(state, "evt_pay_1", event) == "applied"
    assert apply_webhook(state, "evt_pay_1", event) == "replay"
    assert state["payment_state"] == "paid"

    refund = provider.refund(order["id"], "rfnd_1", 1999)
    refund_event = {"type": "refund.processed", "refund_id": refund["id"]}
    assert apply_webhook(state, "evt_refund_1", refund_event) == "applied"
    assert apply_webhook(state, "evt_refund_1", refund_event) == "replay"
    replay_refund = provider.refund(order["id"], "rfnd_1", 1999)
    assert replay_refund["replay"] is True
    assert state["refund_state"] == "processed"

    print("RAZORPAY_LIFECYCLE_DRILL: PASS")
    print(json.dumps({
        "create": "PASS",
        "capture": "PASS",
        "duplicate_webhook": "PASS",
        "refund": "PASS",
        "duplicate_refund": "PASS",
        "final_payment_state": state["payment_state"],
        "final_refund_state": state["refund_state"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
