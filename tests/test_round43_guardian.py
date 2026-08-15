import sys, types, sqlite3, tempfile
from pathlib import Path

fake_db = types.ModuleType('database')
fake_db.now = lambda: '2026-08-15T00:00:00+00:00'
sys.modules['database'] = fake_db

from governance_service import guardian_health

with tempfile.TemporaryDirectory() as td:
    conn = sqlite3.connect(Path(td) / 'g.db')
    conn.row_factory = sqlite3.Row
    conn.executescript('''
      CREATE TABLE business_exceptions(id INTEGER PRIMARY KEY, code TEXT, severity TEXT, title TEXT, description TEXT, entity TEXT, entity_id INTEGER, metadata_json TEXT, status TEXT, resolved_by INTEGER, resolution TEXT, created_at TEXT, resolved_at TEXT, assigned_to INTEGER, due_at TEXT, escalated_at TEXT, escalation_reason TEXT);
      CREATE TABLE guardian_detectors(id INTEGER PRIMARY KEY, code TEXT UNIQUE, title TEXT, severity TEXT, enabled INTEGER, description TEXT, created_at TEXT, updated_at TEXT);
      CREATE TABLE guardian_sla_policies(severity TEXT PRIMARY KEY, due_minutes INTEGER, escalation_grace_minutes INTEGER, notify_assignee INTEGER, notify_admins INTEGER, enabled INTEGER, updated_at TEXT);
      CREATE TABLE exception_events(id INTEGER PRIMARY KEY, exception_id INTEGER, event_type TEXT, actor_id INTEGER, details_json TEXT, created_at TEXT);
      CREATE TABLE admin_users(id INTEGER PRIMARY KEY, username TEXT, is_active INTEGER, role TEXT);
    ''')
    report = guardian_health(conn)
    assert report['ok'] is True
    assert report['status'] == 'healthy'
    assert report['critical_count'] == 0
    conn.close()
print('ROUND43_GUARDIAN_PASS')
