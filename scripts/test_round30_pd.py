"""Dependency-free checks for Round 30."""
import sqlite3
from pathlib import Path
import importlib.util
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("obs", ROOT/"observability_service.py")
obs=importlib.util.module_from_spec(spec); spec.loader.exec_module(obs)
conn=sqlite3.connect(":memory:")
conn.row_factory=sqlite3.Row
obs.ensure_schema(conn)
conn.execute("CREATE TABLE admin_users (id INTEGER PRIMARY KEY, is_active INTEGER NOT NULL DEFAULT 1)")
conn.execute("CREATE TABLE team_notifications (admin_id INTEGER, kind TEXT, title TEXT, body TEXT, created_at TEXT)")
conn.execute("INSERT INTO admin_users(id,is_active) VALUES(1,1)")
obs.emit_alert(conn, trace_id="t1", alert_type="http_5xx", severity="high", title="GET /x", details="boom")
assert conn.execute("SELECT COUNT(*) FROM observability_alerts").fetchone()[0] == 1
assert conn.execute("SELECT COUNT(*) FROM team_notifications").fetchone()[0] == 1
conn.close()
print("ROUND30_PD: PASS")
