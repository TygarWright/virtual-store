import sqlite3, importlib.util, json
from pathlib import Path

ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('gov', ROOT/'governance_service.py')
gov=importlib.util.module_from_spec(spec); spec.loader.exec_module(gov)

class Conn:
    def __init__(self):
        self.c=sqlite3.connect(':memory:')
        self.c.row_factory=sqlite3.Row
    def execute(self,*a,**k): return self.c.execute(*a,**k)
    def commit(self): self.c.commit()


def test_approval_policy_snapshot_is_captured_and_used():
    c=Conn(); c.execute("CREATE TABLE high_risk_action_policies(action TEXT PRIMARY KEY, required_approvals INTEGER, approval_expiry_minutes INTEGER, enabled INTEGER, version INTEGER)")
    c.execute("CREATE TABLE admin_approval_requests(id INTEGER PRIMARY KEY AUTOINCREMENT, request_ref TEXT, requested_by INTEGER, action TEXT, entity TEXT, entity_id INTEGER, amount INTEGER, reason TEXT, metadata_json TEXT, status TEXT, approved_by INTEGER, approval_note TEXT, created_at TEXT, approved_at TEXT, updated_at TEXT, policy_version INTEGER, policy_snapshot_json TEXT)")
    c.execute("CREATE TABLE approval_steps(id INTEGER PRIMARY KEY AUTOINCREMENT, approval_id INTEGER, step_index INTEGER, status TEXT, approved_by INTEGER, note TEXT, approved_at TEXT)")
    c.execute("INSERT INTO high_risk_action_policies VALUES('x',2,1,1,7)")
    aid=gov.create_approval(c, requested_by=1, action='x', entity='order', entity_id=1, amount=10)
    row=c.execute('SELECT policy_version, policy_snapshot_json FROM admin_approval_requests WHERE id=?',(aid,)).fetchone()
    assert row['policy_version']==7
    snap=json.loads(row['policy_snapshot_json']); assert snap['approval_expiry_minutes']==1
