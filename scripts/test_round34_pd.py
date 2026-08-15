import sqlite3, tempfile, json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from titan_workflows import DurableWorkflow, WorkflowStep, WorkflowError
from workflow_recovery import workflow_health
from titan_db_tools import backup, restore, verify_manifest

conn=sqlite3.connect(":memory:"); conn.row_factory=sqlite3.Row
state=[]
flow=DurableWorkflow(conn, workflow_type="test.flow", aggregate_type="test", aggregate_id="1")
flow.run([WorkflowStep("a", lambda c,ctx: {"a":1}), WorkflowStep("b", lambda c,ctx: {"b":2})])
assert flow.status()["status"]=="completed"
# exhausted failed workflow refuses silent re-execution
flow2=DurableWorkflow(conn, workflow_type="test.fail", aggregate_type="test", aggregate_id="2")
def fail(c,ctx): raise RuntimeError("boom")
try: flow2.run([WorkflowStep("fail", fail)])
except WorkflowError: pass
else: raise AssertionError("failure did not surface")
try: flow2.run([WorkflowStep("fail", fail)])
except WorkflowError: pass
else: raise AssertionError("failed workflow retried without explicit recovery")
assert workflow_health(conn)["failed"] >= 1

with tempfile.TemporaryDirectory() as td:
    src=Path(td)/"src.db"; b=Path(td)/"backup.db"; dst=Path(td)/"restored.db"
    c=sqlite3.connect(src); c.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)"); c.execute("INSERT INTO t(v) VALUES('x')"); c.commit(); c.close()
    backup(str(src), str(b))
    ok, probs=verify_manifest(str(b)); assert ok, probs
    restore(str(b), str(dst), force=True)
    ok, probs=verify_manifest(str(dst)); assert ok, probs
print("Round 34 PD checks: PASS")
