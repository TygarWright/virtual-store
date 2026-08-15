import sqlite3, sys, types
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

db=types.ModuleType('database')
def ensure_round43_schema(conn):
    for sql in [
        "ALTER TABLE domain_event_deliveries ADD COLUMN available_at TEXT",
        "ALTER TABLE domain_event_deliveries ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 5",
        "ALTER TABLE domain_event_deliveries ADD COLUMN delivered_at TEXT",
    ]:
        try: conn.execute(sql)
        except sqlite3.OperationalError: pass
    conn.execute("UPDATE domain_event_deliveries SET available_at=COALESCE(available_at,updated_at) WHERE available_at IS NULL")
    conn.commit()
def ensure_guardian_mastery_schema(conn): pass
def ensure_business_exception_columns(conn): pass
def guardian_schema_ready(conn): return True
db.ensure_round43_schema=ensure_round43_schema
db.ensure_guardian_mastery_schema=ensure_guardian_mastery_schema
db.ensure_business_exception_columns=ensure_business_exception_columns
db.guardian_schema_ready=guardian_schema_ready
sys.modules['database']=db

from backend_kernel import validate_business_event, event_spine_contract_report, record_event_delivery, requeue_event_delivery
from governance_service import add_exception, assign_exception, acknowledge_exception, resolve_exception, reopen_exception, guardian_acceptance_check

report=event_spine_contract_report(); assert report['ok'] and report['count'] >= 10
validate_business_event(topic='order.paid', aggregate='order', payload={'order_id':1})
try:
    validate_business_event(topic='order.paid', aggregate='refund', payload={})
except ValueError:
    pass
else:
    raise AssertionError('aggregate mismatch accepted')

c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row
c.executescript("""
CREATE TABLE domain_events(event_id TEXT PRIMARY KEY, topic TEXT, aggregate TEXT, aggregate_id TEXT, payload_json TEXT, created_at TEXT);
CREATE TABLE outbox_jobs(id INTEGER PRIMARY KEY AUTOINCREMENT, job_type TEXT, payload_json TEXT, idempotency_key TEXT UNIQUE, status TEXT, attempts INTEGER, max_attempts INTEGER, available_at TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE domain_event_deliveries(event_id TEXT, consumer TEXT, status TEXT, attempts INTEGER, last_error TEXT, updated_at TEXT, available_at TEXT, max_attempts INTEGER, delivered_at TEXT, PRIMARY KEY(event_id,consumer));
CREATE TABLE guardian_detectors(id INTEGER PRIMARY KEY, code TEXT UNIQUE, title TEXT, severity TEXT, enabled INTEGER, description TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE guardian_sla_policies(severity TEXT PRIMARY KEY, due_minutes INTEGER, escalation_grace_minutes INTEGER, notify_assignee INTEGER, notify_admins INTEGER, enabled INTEGER, updated_at TEXT);
CREATE TABLE business_exceptions(id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, severity TEXT, title TEXT, description TEXT, entity TEXT, entity_id INTEGER, metadata_json TEXT, status TEXT, resolved_by INTEGER, resolution TEXT, created_at TEXT, resolved_at TEXT, assigned_to INTEGER, due_at TEXT, escalated_at TEXT, escalation_reason TEXT, updated_at TEXT);
CREATE TABLE admin_users(id INTEGER PRIMARY KEY, username TEXT, is_active INTEGER);
""")
for row in [('critical',60,15,1,1,1),('high',240,30,1,1,1),('medium',1440,60,1,1,1),('low',4320,120,1,0,1)]:
    c.execute('INSERT INTO guardian_sla_policies VALUES(?,?,?,?,?,?,?)', (*row,'2026'))
for row in [('a','A','critical'),('b','B','high'),('c','C','medium')]:
    c.execute("INSERT INTO guardian_detectors(code,title,severity,enabled,description,created_at,updated_at) VALUES(?,?,?,1,'','2026','2026')", row)
c.execute("INSERT INTO admin_users VALUES(1,'owner',1)")
c.commit()

eid=add_exception(c,code='acceptance',severity='high',title='Acceptance',description='x',entity='order',entity_id=1)
assert assign_exception(c,eid,assigned_to=1,actor_id=1)
assert acknowledge_exception(c,eid,admin_id=1)
assert resolve_exception(c,eid,resolved_by=1,resolution='verified')
assert reopen_exception(c,eid,admin_id=1,reason='regression check')
topics=[r[0] for r in c.execute("SELECT topic FROM domain_events WHERE aggregate='exception' ORDER BY rowid").fetchall()]
for expected in ['governance.exception.created','governance.exception.assigned','governance.exception.acknowledged','governance.exception.resolved','governance.exception.reopened']:
    assert expected in topics, (expected, topics)
r=guardian_acceptance_check(c)
assert r['ok'] is True, r

c.execute("INSERT INTO domain_event_deliveries VALUES('evt','consumer','dead_letter',3,'boom','2026','2026',3,NULL)")
try:
    record_event_delivery(c,event_id='evt',consumer='consumer',status='processed',max_attempts=3)
except RuntimeError:
    pass
else:
    raise AssertionError('dead-letter was implicitly resurrected')
assert requeue_event_delivery(c,event_id='evt',consumer='consumer') is True
record_event_delivery(c,event_id='evt',consumer='consumer',status='processed',max_attempts=3)
row=c.execute("SELECT status,delivered_at FROM domain_event_deliveries WHERE event_id='evt'").fetchone()
assert row['status']=='processed' and row['delivered_at']
print('ROUND47_PD_CLOSURE_PASS')
