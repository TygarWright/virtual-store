import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Import governance_service without the full Flask/Werkzeug runtime.
stub_db = types.SimpleNamespace(now=lambda: "2026-08-15T00:00:00+00:00")
sys.modules['database'] = stub_db

from backend_kernel import publish_event

def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

gov = load_module('governance_round46', 'governance_service.py')
mem = load_module('mastery_round46', 'mastery_services.py')


def row_factory(conn):
    conn.row_factory = sqlite3.Row


def governance_schema():
    return '''
    CREATE TABLE high_risk_action_policies(action TEXT PRIMARY KEY, threshold_amount INTEGER, require_two_person INTEGER, required_approvals INTEGER, approval_expiry_minutes INTEGER, enabled INTEGER, version INTEGER, updated_at TEXT);
    CREATE TABLE admin_users(id INTEGER PRIMARY KEY, username TEXT, is_active INTEGER DEFAULT 1);
    CREATE TABLE admin_approval_requests(id INTEGER PRIMARY KEY AUTOINCREMENT, request_ref TEXT UNIQUE, requested_by INTEGER, action TEXT, entity TEXT, entity_id INTEGER, amount INTEGER, reason TEXT, metadata_json TEXT, status TEXT, approved_by INTEGER, approval_note TEXT, created_at TEXT, approved_at TEXT, updated_at TEXT, policy_version INTEGER, policy_snapshot_json TEXT);
    CREATE TABLE approval_steps(id INTEGER PRIMARY KEY AUTOINCREMENT, approval_id INTEGER, step_index INTEGER, status TEXT, approved_by INTEGER, note TEXT, approved_at TEXT, UNIQUE(approval_id, step_index));
    CREATE TABLE domain_events(event_id TEXT PRIMARY KEY, topic TEXT, aggregate TEXT, aggregate_id TEXT, payload_json TEXT, created_at TEXT);
    '''


def memory_schema():
    return '''
    CREATE TABLE decision_journal(id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, title TEXT, decision TEXT, reason TEXT, expected_result TEXT, outcome TEXT DEFAULT '', lesson TEXT DEFAULT '', reviewed_by INTEGER, reviewed_at TEXT, future_recommendation TEXT DEFAULT '', effectiveness TEXT DEFAULT 'unreviewed', effectiveness_score INTEGER, review_due_at TEXT, created_at TEXT);
    CREATE TABLE institutional_memory_index(id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT, source_id INTEGER, title TEXT, body TEXT, keywords TEXT, created_at TEXT, updated_at TEXT, UNIQUE(source_type, source_id));
    CREATE TABLE feature_flags(key TEXT PRIMARY KEY, description TEXT, enabled INTEGER, rollout_percent INTEGER, updated_by INTEGER, updated_at TEXT);
    CREATE TABLE experiments(id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, name TEXT, status TEXT, variants_json TEXT, allocation_json TEXT, primary_metric TEXT, started_at TEXT, ended_at TEXT, created_by INTEGER, created_at TEXT, updated_at TEXT);
    CREATE TABLE experiment_assignments(id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id INTEGER, subject_key TEXT, variant TEXT, assigned_at TEXT);
    '''


def test_governance_policy_snapshot_and_multi_step():
    c = sqlite3.connect(':memory:'); row_factory(c); c.executescript(governance_schema())
    c.executemany("INSERT INTO admin_users(id,username,is_active) VALUES(?,?,1)", [(1,'owner'),(2,'a'),(3,'b')])
    c.execute("INSERT INTO high_risk_action_policies VALUES('refund.execute',0,1,2,60,1,7,'2026')")
    approval_id = gov.create_approval(c, requested_by=1, action='refund.execute', entity='order', entity_id=99, amount=5000, metadata={'refund_amount':5000})
    r=c.execute("SELECT policy_version,policy_snapshot_json,metadata_json FROM admin_approval_requests WHERE id=?",(approval_id,)).fetchone()
    assert r['policy_version'] == 7
    assert json.loads(r['policy_snapshot_json'])['required_approvals'] == 2
    assert json.loads(r['metadata_json'])['business_metadata']['refund_amount'] == 5000
    assert gov.approve(c, approval_id, approved_by=2, note='first') is True
    assert c.execute("SELECT status FROM admin_approval_requests WHERE id=?",(approval_id,)).fetchone()[0] == 'pending'
    assert gov.approve(c, approval_id, approved_by=2) is False
    assert gov.approve(c, approval_id, approved_by=3, note='second') is True
    assert c.execute("SELECT status FROM admin_approval_requests WHERE id=?",(approval_id,)).fetchone()[0] == 'approved'
    # Exact business metadata must be enforced on the execution gate.
    assert gov.request_or_validate_approval(c, action='refund.execute', requested_by=1, entity='order', entity_id=99, amount=5000, metadata={'refund_amount':4999}, approval_id=approval_id)['allowed'] is False
    assert gov.request_or_validate_approval(c, action='refund.execute', requested_by=1, entity='order', entity_id=99, amount=5000, metadata={'refund_amount':5000}, approval_id=approval_id)['allowed'] is True


def test_institutional_memory_history_links_and_health():
    c=sqlite3.connect(':memory:'); row_factory(c); c.executescript(memory_schema())
    c.execute("INSERT INTO decision_journal(admin_id,title,decision,reason,expected_result,created_at) VALUES(1,'Refund policy','Cap refunds','Fraud risk','Lower losses','2026-08-01T00:00:00+00:00')")
    c.execute("INSERT INTO institutional_memory_index(source_type,source_id,title,body,keywords,created_at,updated_at) VALUES('sop',10,'Refund policy SOP','Reduce fraud','refund policy fraud','2026-08-01','2026-08-01')")
    mem.index_memory(c,source_type='decision',source_id=1,title='Refund policy',body='Cap refunds Lower losses',keywords='refund policy fraud')
    mem.record_decision_outcome(c,decision_id=1,outcome='Losses fell',lesson='Manual review works',future_recommendation='Keep cap',reviewed_by=2,effectiveness='effective',effectiveness_score=92)
    mem.record_decision_outcome(c,decision_id=1,outcome='Still effective',lesson='Keep monitoring',future_recommendation='Review quarterly',reviewed_by=3,effectiveness='mixed',effectiveness_score=71)
    history=mem.decision_review_history(c,1)
    assert len(history)==2
    assert history[0]['effectiveness'] == 'mixed'
    related=mem.related_memory(c,'decision',1)
    assert related and related[0]['related_id'] == 10
    report=mem.decision_effectiveness_report(c)
    assert report['reviewed'] == 1
    assert report['effectiveness']['mixed'] == 1

print('ROUND46_PD_CLOSURE_PASS')
