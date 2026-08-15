from pathlib import Path
import ast, sqlite3, sys
src=Path(__file__).resolve().parents[1]
for f in src.rglob("*.py"):
    ast.parse(f.read_text(encoding="utf-8"))
sys.path.insert(0,str(src))
import governance_service as gs
conn=sqlite3.connect(":memory:"); conn.row_factory=sqlite3.Row
conn.executescript("CREATE TABLE admin_users(id INTEGER PRIMARY KEY, username TEXT, is_active INTEGER DEFAULT 1); CREATE TABLE simulation_runs(id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, label TEXT, parameters_json TEXT, results_json TEXT, status TEXT, created_at TEXT);")
gs.ensure_operations_lab_schema(conn)
assert len(gs.simulation_catalog(conn))==4
r=gs.simulate_scenario(conn, admin_id=1, scenario="payment_outage", scale=1000)
rep=gs.simulation_report(conn,r["id"])
assert rep["risk"]=="critical" and rep["acceptance_checks"]
att=gs.record_training_attempt(conn, admin_id=1, scenario_key="payment_outage", answer="check provider state; reconcile before retry; do not refund blindly")
assert att["passed"] and att["score"]==100
summary=gs.training_report(conn, admin_id=1)
assert summary["attempts"]==1 and summary["passed"]==1
print("ROUND53_PD_CLOSURE_PASS")
