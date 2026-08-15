from pathlib import Path
import sqlite3, sys
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from governance_service import ensure_guardian_mastery_schema, add_exception, guardian_sla_policy, escalate_overdue_exceptions, record_recovery_action, recovery_action_history
conn=sqlite3.connect(':memory:'); conn.row_factory=sqlite3.Row
conn.executescript('''
CREATE TABLE admin_users(id INTEGER PRIMARY KEY, username TEXT, is_active INTEGER DEFAULT 1, role TEXT DEFAULT 'admin');
INSERT INTO admin_users(id,username,is_active,role) VALUES(1,'owner',1,'master'),(2,'staff',1,'admin');
CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT, email TEXT, phone TEXT, created_at TEXT, last_login_at TEXT);
CREATE TABLE business_exceptions(id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, severity TEXT, title TEXT, description TEXT, entity TEXT DEFAULT '', entity_id INTEGER DEFAULT 0, metadata_json TEXT DEFAULT '{}', status TEXT DEFAULT 'open', resolved_by INTEGER, resolution TEXT DEFAULT '', created_at TEXT, resolved_at TEXT, assigned_to INTEGER, due_at TEXT, escalated_at TEXT, escalation_reason TEXT DEFAULT '', updated_at TEXT);
CREATE TABLE team_notifications(id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, kind TEXT, title TEXT, body TEXT, created_at TEXT);
CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER, order_ref TEXT, amount INTEGER, status TEXT, payment_state TEXT, payment_mode TEXT, created_at TEXT, order_state TEXT);
CREATE TABLE order_payments(id INTEGER PRIMARY KEY, order_id INTEGER, status TEXT);
CREATE TABLE order_refunds(id INTEGER PRIMARY KEY, order_id INTEGER, amount INTEGER, reason TEXT, status TEXT, initiated_at TEXT);
CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT, quantity INTEGER);
CREATE TABLE support_interactions(id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER, admin_id INTEGER, channel TEXT, subject TEXT, summary TEXT, outcome TEXT, created_at TEXT);
''')
ensure_guardian_mastery_schema(conn)
assert guardian_sla_policy(conn,'high')['due_minutes'] == 240
ex=add_exception(conn,code='test',severity='high',title='Test',description='Test')
row=conn.execute('SELECT due_at FROM business_exceptions WHERE id=?',(ex,)).fetchone()
assert row['due_at']
# force overdue then escalate
conn.execute("UPDATE business_exceptions SET due_at='2000-01-01T00:00:00Z', assigned_to=2 WHERE id=?",(ex,)); conn.commit()
assert escalate_overdue_exceptions(conn, now_iso='2026-08-15T00:00:00Z') == 1
assert conn.execute("SELECT count(*) c FROM team_notifications WHERE admin_id=2 AND kind='guardian_escalation'").fetchone()['c'] == 1
# recovery action persistence
rid=record_recovery_action(conn,customer_id=1,admin_id=2,playbook_key='payment_recovery',step_index=0,action='Verify provider state',outcome='Checked')
assert recovery_action_history(conn,1)[0]['id']==rid
print('Round 36 PD checks: PASS')
