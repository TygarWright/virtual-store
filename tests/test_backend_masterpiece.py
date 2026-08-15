import sqlite3
from datetime import datetime, timezone

from backend_kernel import begin_idempotent_operation, finish_idempotent_operation, publish_event


def test_idempotency_replays_and_rejects_payload_conflict():
    c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row
    c.execute("CREATE TABLE idempotency_keys (id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'processing', result_json TEXT NOT NULL DEFAULT '{}', expires_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(namespace,idempotency_key))")
    first=begin_idempotent_operation(c, namespace='test', key='abc', payload={'a':1})
    assert first['state']=='claimed'
    finish_idempotent_operation(c, namespace='test', key='abc', result={'ok':True})
    second=begin_idempotent_operation(c, namespace='test', key='abc', payload={'a':1})
    assert second['state']=='complete'
    conflict=begin_idempotent_operation(c, namespace='test', key='abc', payload={'a':2})
    assert conflict['state']=='conflict'


def test_domain_event_creates_event_and_outbox():
    c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row
    c.execute("CREATE TABLE domain_events (event_id TEXT PRIMARY KEY, topic TEXT NOT NULL, aggregate TEXT NOT NULL, aggregate_id TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL)")
    c.execute("CREATE TABLE outbox_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, job_type TEXT, payload_json TEXT, idempotency_key TEXT UNIQUE, status TEXT, attempts INTEGER, max_attempts INTEGER, available_at TEXT, created_at TEXT, updated_at TEXT)")
    event_id=publish_event(c, topic='order.created', aggregate='order', aggregate_id=7, payload={'order_id':7})
    assert c.execute('SELECT COUNT(*) FROM domain_events').fetchone()[0]==1
    row=c.execute('SELECT * FROM outbox_jobs').fetchone()
    assert row['idempotency_key']==f'domain-event:{event_id}'
