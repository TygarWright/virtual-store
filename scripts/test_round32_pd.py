import sqlite3
from pathlib import Path
root=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(root))
from governance_service import ensure_guardian_mastery_schema, add_exception, record_exception_event, exception_timeline, guardian_detectors
conn=sqlite3.connect(':memory:'); conn.row_factory=sqlite3.Row
conn.executescript("CREATE TABLE admin_users(id INTEGER PRIMARY KEY, username TEXT, is_active INTEGER DEFAULT 1); CREATE TABLE business_exceptions(id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, severity TEXT, title TEXT, description TEXT, entity TEXT DEFAULT '', entity_id INTEGER DEFAULT 0, metadata_json TEXT DEFAULT '{}', status TEXT DEFAULT 'open', resolved_by INTEGER, resolution TEXT DEFAULT '', created_at TEXT, resolved_at TEXT, assigned_to INTEGER, due_at TEXT, escalated_at TEXT, escalation_reason TEXT DEFAULT '', updated_at TEXT);")
ensure_guardian_mastery_schema(conn); eid=add_exception(conn,code='x',severity='high',title='X',description='x'); record_exception_event(conn,eid,'created',details={'source':'test'}); assert len(exception_timeline(conn,eid))==1; assert len(guardian_detectors(conn))>=3
from observability_service import ensure_alert_policy_schema,set_alert_policy,alert_policies
ensure_alert_policy_schema(conn); set_alert_policy(conn,alert_type='test',enabled=True,severity='high',cooldown_minutes=7,notify_admins=True); assert any(r['alert_type']=='test' for r in alert_policies(conn))
print('Round 32 PD checks: PASS')
