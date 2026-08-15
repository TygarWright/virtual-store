"""Deterministic Event Spine acceptance helpers."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend_kernel import CORE_EVENT_SPECS, validate_business_event, record_event_delivery, requeue_event_delivery

def acceptance():
    assert CORE_EVENT_SPECS["order.paid"] == "order"
    validate_business_event(topic="order.paid", aggregate="order", payload={"order_id": 1})
    validate_business_event(topic="custom.demo", aggregate="demo", payload={})
    try:
        validate_business_event(topic="order.paid", aggregate="refund", payload={})
    except ValueError:
        pass
    else:
        raise AssertionError("aggregate mismatch must be rejected")
    return True

if __name__ == "__main__":
    acceptance(); print("EVENT_SPINE_ACCEPTANCE_PASS")
