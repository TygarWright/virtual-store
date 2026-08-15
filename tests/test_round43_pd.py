import sys, types, sqlite3, tempfile
from pathlib import Path

fake_db = types.ModuleType("database")
def ensure_round43_schema(conn):
    for sql in [
        "ALTER TABLE domain_event_deliveries ADD COLUMN available_at TEXT",
        "ALTER TABLE domain_event_deliveries ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 5",
        "ALTER TABLE domain_event_deliveries ADD COLUMN delivered_at TEXT",
    ]:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    conn.execute("UPDATE domain_event_deliveries SET available_at=COALESCE(available_at, updated_at) WHERE available_at IS NULL")
    conn.commit()
fake_db.ensure_round43_schema = ensure_round43_schema
sys.modules["database"] = fake_db

from backend_kernel import record_event_delivery, retryable_event_deliveries, dead_letter_event_deliveries

with tempfile.TemporaryDirectory() as td:
    conn = sqlite3.connect(Path(td) / "t.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE domain_events(event_id TEXT PRIMARY KEY, topic TEXT NOT NULL, aggregate TEXT NOT NULL, aggregate_id TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE domain_event_deliveries(event_id TEXT NOT NULL, consumer TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL, PRIMARY KEY(event_id,consumer));
        INSERT INTO domain_events VALUES ('evt-1','test.created','x','1','{}','2026-08-15T00:00:00+00:00');
        """
    )
    record_event_delivery(conn, event_id="evt-1", consumer="tester", status="failed", error="boom", max_attempts=2)
    row = conn.execute("SELECT status, attempts FROM domain_event_deliveries").fetchone()
    assert row["status"] == "failed" and row["attempts"] == 1
    assert len(retryable_event_deliveries(conn, consumer="tester")) == 0
    record_event_delivery(conn, event_id="evt-1", consumer="tester", status="failed", error="boom2", max_attempts=2)
    assert len(dead_letter_event_deliveries(conn)) == 1
    record_event_delivery(conn, event_id="evt-1", consumer="tester", status="processed", max_attempts=2)
    assert len(dead_letter_event_deliveries(conn)) == 0
    assert conn.execute("SELECT delivered_at FROM domain_event_deliveries").fetchone()[0]
    conn.close()

print("ROUND43_PD_PASS")
