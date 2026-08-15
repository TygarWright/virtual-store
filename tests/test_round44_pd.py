import sqlite3, tempfile, sys, types
from pathlib import Path

fake_db=types.ModuleType("database")
fake_db.ensure_guardian_mastery_schema=lambda conn: None
sys.modules["database"]=fake_db
from governance_service import guardian_cross_signal_summary
from backend_kernel import publish_business_event, list_domain_events

with tempfile.TemporaryDirectory() as td:
    c=sqlite3.connect(Path(td)/"t.db"); c.row_factory=sqlite3.Row
    c.executescript("""
    CREATE TABLE domain_events(event_id TEXT PRIMARY KEY, topic TEXT NOT NULL, aggregate TEXT NOT NULL, aggregate_id TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE outbox_jobs(job_type TEXT,payload_json TEXT,idempotency_key TEXT,status TEXT,attempts INTEGER,max_attempts INTEGER,available_at TEXT,created_at TEXT,updated_at TEXT);
    CREATE TABLE business_exceptions(code TEXT,severity TEXT,entity TEXT,entity_id INTEGER,status TEXT,created_at TEXT);
    """)
    publish_business_event(c, topic="order.paid", aggregate="order", aggregate_id=7, payload={"order_id":7})
    c.execute("INSERT INTO business_exceptions VALUES ('payment_mismatch','high','order',7,'open','2026-08-15T00:00:00+00:00')")
    c.commit()
    assert len(list_domain_events(c, aggregate="order")) == 1
    report=guardian_cross_signal_summary(c)
    assert report["correlated_exceptions"] == 1
    assert report["correlations"][0]["recent_topics"] == ["order.paid"]
print("ROUND44_PD_PASS")
