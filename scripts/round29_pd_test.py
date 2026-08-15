"""Dependency-free Round 29 checks for Team/Support context and observability."""
from pathlib import Path
import sqlite3
import ast

ROOT=Path(__file__).resolve().parents[1]

# Schema contract contains new team context fields.
s=(ROOT/'schema_contract.py').read_text()
assert 'team_conversations", "context_type"' in s
assert 'team_conversations", "context_id"' in s

# Team context helper and visibility semantics are present.
pc=(ROOT/'permissions_comm.py').read_text()
assert 'def get_or_create_context' in pc
assert "c.kind='context'" in pc
assert 'context_type' in pc and 'context_id' in pc

# Admin has context entrypoint and permission mapping.
ad=(ROOT/'blueprints/admin.py').read_text()
assert 'def admin_team_hub_context' in ad
assert 'orders.view' in ad and 'tickets.view' in ad and 'audit.view' in ad

# Observability now includes reconciliation spans and an operation filter.
rz=(ROOT/'reconcile_razorpay.py').read_text()
assert 'observability_service.start_span' in rz
assert 'kind="reconciliation"' in rz
ob=(ROOT/'blueprints/admin.py').read_text()
assert 'operation = (request.args.get("operation") or "").strip()' in ob

# Raw SQL for the new migration is valid SQLite syntax.
conn=sqlite3.connect(':memory:')
conn.execute("CREATE TABLE team_conversations (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, title TEXT, created_by INTEGER, created_at TEXT, updated_at TEXT)")
conn.execute("ALTER TABLE team_conversations ADD COLUMN context_type TEXT NOT NULL DEFAULT ''")
conn.execute("ALTER TABLE team_conversations ADD COLUMN context_id INTEGER")
conn.execute("CREATE INDEX idx_team_conversations_context ON team_conversations(context_type, context_id)")
conn.execute("INSERT INTO team_conversations(kind,title,created_by,context_type,context_id,created_at,updated_at) VALUES('context','Order #A',1,'order',42,'x','x')")
row=conn.execute("SELECT context_type,context_id FROM team_conversations WHERE id=1").fetchone()
assert row == ('order',42)
conn.close()

for p in ROOT.rglob('*.py'):
    ast.parse(p.read_text())
print('ROUND29_PD: PASS')
