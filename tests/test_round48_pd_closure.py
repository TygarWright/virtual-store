import sqlite3
import sys
import types
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def _workflow_conn():
    c=sqlite3.connect(":memory:")
    c.row_factory=sqlite3.Row
    return c


def test_workflow_crash_resume_and_attempt_budget():
    from titan_workflows import DurableWorkflow, WorkflowStep, WorkflowError
    c=_workflow_conn()
    calls=[]
    def first(conn, ctx):
        calls.append("first")
        return {"prepared": True}
    fail=[True]
    def second(conn, ctx):
        calls.append("second")
        if fail[0]:
            fail[0]=False
            raise RuntimeError("simulated crash")
        return {"done": True}
    flow=DurableWorkflow(c, workflow_type="test.recovery", aggregate_type="test", aggregate_id="1")
    try:
        flow.run([WorkflowStep("first", first), WorkflowStep("second", second)])
    except WorkflowError:
        pass
    else:
        raise AssertionError("first attempt should fail")
    row=c.execute("SELECT status,current_step,attempt_count FROM workflow_runs WHERE workflow_type='test.recovery'").fetchone()
    assert row["status"]=="failed" and row["current_step"]==1 and row["attempt_count"]>=2
    result=flow.run([WorkflowStep("first", first), WorkflowStep("second", second)])
    assert result["status"]=="completed"
    assert calls == ["first","second","second"], calls


def test_workflow_max_attempts_requires_explicit_recovery():
    from titan_workflows import DurableWorkflow, WorkflowStep, WorkflowError
    c=_workflow_conn(); flow=DurableWorkflow(c, workflow_type="test.budget", aggregate_type="test", aggregate_id="2")
    for _ in range(3):
        try:
            flow.run([WorkflowStep("always_fail", lambda conn,ctx: (_ for _ in ()).throw(RuntimeError("boom")))])
        except WorkflowError:
            pass
    try:
        flow.run([WorkflowStep("always_fail", lambda conn,ctx: (_ for _ in ()).throw(RuntimeError("boom")))])
    except WorkflowError as exc:
        assert "exhausted max attempts" in str(exc)
    else:
        raise AssertionError("workflow should refuse implicit retry after budget exhaustion")


def test_reconciliation_lock_prevents_overlap_and_releases():
    # Use a fake database module so reconcile_razorpay can be imported without Flask/Werkzeug.
    fake_db=types.ModuleType("database")
    fake_db.get_db=lambda: None
    fake_db.ensure_round18_schema=lambda conn: None
    fake_db.ensure_round24_schema=lambda conn: None
    fake_db.ensure_round45_schema=lambda conn: None
    fake_db.ensure_round48_schema=lambda conn: None
    sys.modules["database"]=fake_db
    fake_rzp=types.ModuleType("razorpay_client"); fake_rzp.is_configured=lambda: False; sys.modules["razorpay_client"]=fake_rzp
    fake_gov=types.ModuleType("governance_service"); fake_gov.open_exception=lambda *a,**k: None; sys.modules["governance_service"]=fake_gov
    fake_obs=types.ModuleType("observability_service"); sys.modules["observability_service"]=fake_obs
    import reconcile_razorpay
    c=sqlite3.connect(":memory:"); c.row_factory=sqlite3.Row
    c.execute("CREATE TABLE reconciliation_locks(provider TEXT PRIMARY KEY, acquired_until TEXT NOT NULL, acquired_at TEXT NOT NULL, acquired_by INTEGER)")
    assert reconcile_razorpay.acquire_reconciliation_lock(c, provider="razorpay", acquired_by=1)
    assert not reconcile_razorpay.acquire_reconciliation_lock(c, provider="razorpay", acquired_by=2)
    reconcile_razorpay.release_reconciliation_lock(c, provider="razorpay")
    assert reconcile_razorpay.acquire_reconciliation_lock(c, provider="razorpay", acquired_by=2)


def test_reconciliation_ledger_snapshot_is_idempotent():
    fake_db=sys.modules.get("database")
    if fake_db is None:
        fake_db=types.ModuleType("database"); sys.modules["database"]=fake_db
        fake_db.get_db=lambda: None
    import reconcile_razorpay
    c=sqlite3.connect(":memory:"); c.row_factory=sqlite3.Row
    c.executescript("""
    CREATE TABLE financial_ledger(entry_key TEXT PRIMARY KEY, entry_type TEXT, order_id INTEGER, refund_id INTEGER, provider TEXT, provider_reference TEXT, amount INTEGER, currency TEXT, occurred_at TEXT, created_at TEXT, metadata_json TEXT);
    CREATE TABLE financial_ledger_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, period_start TEXT, period_end TEXT, gross_sales INTEGER, refunds INTEGER, net_sales INTEGER, ledger_entries INTEGER, created_at TEXT, UNIQUE(period_start,period_end));
    """)
    reconcile_razorpay._ledger_entry(c, entry_key="sale:1:p1", entry_type="sale", order_id=1, provider_reference="p1", amount=100)
    reconcile_razorpay._ledger_entry(c, entry_key="sale:1:p1", entry_type="sale", order_id=1, provider_reference="p1", amount=100)
    row=reconcile_razorpay.refresh_ledger_snapshot(c, period_start="0000", period_end="9999")
    assert row["gross_sales"]==100 and row["ledger_entries"]==1
