import sqlite3, tempfile, types, sys
from pathlib import Path

# Minimal DB facade for event functions
fake_db=types.ModuleType('database')
def ensure_round43_schema(conn):
    try: conn.execute("ALTER TABLE domain_event_deliveries ADD COLUMN available_at TEXT")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE domain_event_deliveries ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 5")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE domain_event_deliveries ADD COLUMN delivered_at TEXT")
    except sqlite3.OperationalError: pass
    conn.execute("UPDATE domain_event_deliveries SET available_at=COALESCE(available_at,updated_at) WHERE available_at IS NULL")
    conn.commit()
fake_db.ensure_round43_schema=ensure_round43_schema
def ensure_business_exception_columns(conn): pass
def guardian_schema_ready(conn): return True
fake_db.ensure_business_exception_columns=ensure_business_exception_columns
fake_db.guardian_schema_ready=guardian_schema_ready
sys.modules['database']=fake_db
from backend_kernel import validate_business_event, requeue_event_delivery
from governance_service import guardian_acceptance_check

def test_event_contract_and_dead_letter_requeue():
    c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row
    c.executescript("CREATE TABLE domain_event_deliveries(event_id TEXT,consumer TEXT,status TEXT,attempts INTEGER,last_error TEXT,updated_at TEXT,available_at TEXT,max_attempts INTEGER,delivered_at TEXT,PRIMARY KEY(event_id,consumer));")
    validate_business_event(topic='order.paid', aggregate='order', payload={'order_id':1})
    try: validate_business_event(topic='order.paid', aggregate='refund', payload={})
    except ValueError: pass
    else: raise AssertionError
    c.execute("INSERT INTO domain_event_deliveries VALUES('e1','c1','dead_letter',3,'boom','2026-01-01','2026-01-01',3,NULL)")
    assert requeue_event_delivery(c,event_id='e1',consumer='c1') is True
    assert c.execute("SELECT status FROM domain_event_deliveries").fetchone()[0]=='pending'

def test_guardian_acceptance():
    c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row
    c.executescript("""
    CREATE TABLE business_exceptions(id INTEGER PRIMARY KEY, code TEXT, severity TEXT, title TEXT, description TEXT, entity TEXT, entity_id INTEGER, metadata_json TEXT, status TEXT, resolved_by INTEGER, resolution TEXT, created_at TEXT, resolved_at TEXT, assigned_to INTEGER, due_at TEXT, escalated_at TEXT, escalation_reason TEXT, updated_at TEXT);
    CREATE TABLE admin_users(id INTEGER PRIMARY KEY, username TEXT, is_active INTEGER);
    CREATE TABLE guardian_detectors(id INTEGER PRIMARY KEY, code TEXT UNIQUE, title TEXT, severity TEXT, enabled INTEGER, description TEXT, created_at TEXT, updated_at TEXT);
    CREATE TABLE guardian_sla_policies(severity TEXT PRIMARY KEY, due_minutes INTEGER, escalation_grace_minutes INTEGER, notify_assignee INTEGER, notify_admins INTEGER, enabled INTEGER, updated_at TEXT);
    CREATE TABLE domain_events(event_id TEXT PRIMARY KEY, topic TEXT, aggregate TEXT, aggregate_id TEXT, payload_json TEXT, created_at TEXT);
    INSERT INTO guardian_detectors VALUES(1,'a','A','critical',1,'','2026','2026'),(2,'b','B','high',1,'','2026','2026'),(3,'c','C','medium',1,'','2026','2026');
    INSERT INTO guardian_sla_policies VALUES('critical',60,15,1,1,1,'2026'),('high',240,30,1,1,1,'2026'),('medium',1440,60,1,1,1,'2026'),('low',4320,120,1,0,1,'2026');
    """)
    result=guardian_acceptance_check(c)
    assert result['ok'], result
