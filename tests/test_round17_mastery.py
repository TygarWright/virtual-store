import sqlite3
import pytest

from titan_invariants import InvariantViolation, assert_refund_within_paid, assert_margin_floor
from titan_workflows import DurableWorkflow, WorkflowStep, WorkflowError


def test_business_invariants():
    order = {"amount": 1000, "refunded_amount": 200}
    assert assert_refund_within_paid(order, 800) == 800
    with pytest.raises(InvariantViolation):
        assert_refund_within_paid(order, 801)
    assert assert_margin_floor(sale_price=1000, cost_price=500, min_margin_percent=20, discount_amount=100) == 900
    with pytest.raises(InvariantViolation):
        assert_margin_floor(sale_price=1000, cost_price=900, min_margin_percent=20, discount_amount=100)


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


def test_durable_workflow_resume():
    conn = _conn()
    calls = []
    flow = DurableWorkflow(conn, workflow_type="demo", aggregate_type="order", aggregate_id="1", workflow_id="w1")

    def one(c, ctx):
        calls.append("one")
        return {"a": 1}

    def two(c, ctx):
        calls.append("two")
        return {"b": 2}

    result = flow.run([WorkflowStep("one", one), WorkflowStep("two", two)])
    assert result["status"] == "completed"
    assert calls == ["one", "two"]
    result2 = flow.run([WorkflowStep("one", lambda *_: calls.append("bad")), WorkflowStep("two", lambda *_: calls.append("bad2"))])
    assert result2["status"] == "completed"
    assert calls == ["one", "two"]


def test_durable_workflow_failure_is_recorded_and_resumable():
    conn = _conn()
    flow = DurableWorkflow(conn, workflow_type="demo", aggregate_type="order", aggregate_id="1", workflow_id="w2")
    calls = []
    def one(c, ctx):
        calls.append("one")
    def boom(c, ctx):
        calls.append("boom")
        raise RuntimeError("boom")
    with pytest.raises(WorkflowError):
        flow.run([WorkflowStep("one", one), WorkflowStep("boom", boom)])
    row = conn.execute("select status,current_step from workflow_runs where workflow_id='w2'").fetchone()
    assert row["status"] == "failed"
    assert row["current_step"] == 1


def test_workflow_step_context_survives():
    conn = _conn()
    flow = DurableWorkflow(conn, workflow_type="demo", aggregate_type="order", aggregate_id="1", workflow_id="w3")
    result = flow.run([
        WorkflowStep("seed", lambda *_: {"payment_id": "pay_1"}),
        WorkflowStep("finish", lambda _c, ctx: {"confirmed": ctx["payment_id"] == "pay_1"}),
    ])
    assert result["context"] == {"payment_id": "pay_1", "confirmed": True}
