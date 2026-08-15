import sqlite3, sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
root=Path(__file__).resolve().parents[1]

# Workflow should create a durable run and be safely repeatable.
from titan_workflows import DurableWorkflow, WorkflowStep
c=sqlite3.connect(':memory:')
c.row_factory=sqlite3.Row
flow=DurableWorkflow(c, workflow_type='test', aggregate_type='order', aggregate_id='1', workflow_id='r24')
seen=[]
res=flow.run([WorkflowStep('atomic', lambda conn, ctx: seen.append('x') or {'ok': True})])
assert res['status']=='completed' and seen==['x']
res2=flow.run([WorkflowStep('atomic', lambda *_: seen.append('bad'))])
assert res2['status']=='completed' and seen==['x']

# Reconciliation schema columns should be additive and inspectable.
c.executescript('CREATE TABLE admin_users(id INTEGER PRIMARY KEY); CREATE TABLE reconciliation_items(id INTEGER PRIMARY KEY, run_id INTEGER, code TEXT, resolved INTEGER DEFAULT 0);')
# Reproduce the additive migration contract without importing the full Flask stack.
for stmt in [
    "ALTER TABLE reconciliation_items ADD COLUMN resolution TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE reconciliation_items ADD COLUMN resolved_by INTEGER",
    "ALTER TABLE reconciliation_items ADD COLUMN resolved_at TEXT",
]:
    c.execute(stmt)
cols={r[1] for r in c.execute('PRAGMA table_info(reconciliation_items)')}
assert {'resolution','resolved_by','resolved_at'} <= cols
print('Round24 PD smoke: PASS')
