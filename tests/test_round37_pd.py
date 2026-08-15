import sqlite3
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

root=Path(__file__).resolve().parents[1]
c=sqlite3.connect(":memory:")
c.executescript("""
CREATE TABLE orders(id INTEGER PRIMARY KEY, order_ref TEXT, amount INTEGER, paid_at TEXT, created_at TEXT, razorpay_order_id TEXT, razorpay_payment_id TEXT, status TEXT);
CREATE TABLE order_refunds(id INTEGER PRIMARY KEY, order_id INTEGER, amount INTEGER, provider_refund_id TEXT, status TEXT);
CREATE TABLE admin_users(id INTEGER PRIMARY KEY);
""")
ledger_sql=(root/'database.py').read_text()
assert 'CREATE TABLE IF NOT EXISTS financial_ledger' in ledger_sql
from reconcile_razorpay import _ledger_entry, refresh_ledger_snapshot
_ledger_entry(c, entry_key='sale:1:p1', entry_type='sale', order_id=1, provider_reference='p1', amount=500, occurred_at='2026-01-01T00:00:00+00:00')
_ledger_entry(c, entry_key='refund:1:r1', entry_type='refund', order_id=1, provider_reference='r1', amount=100, occurred_at='2026-01-02T00:00:00+00:00')
row=refresh_ledger_snapshot(c, period_start='0000', period_end='9999')
assert row['gross_sales']==500 and row['refunds']==100 and row['net_sales']==400
from titan_workflows import DurableWorkflow, WorkflowStep
flow=DurableWorkflow(c, workflow_type='order.refund', aggregate_type='refund', aggregate_id='9')
res=flow.run([WorkflowStep('process_refund', lambda conn,ctx: {'result': {'success': True}})])
assert res['status']=='completed'
print('Round37 PD checks: PASS')
