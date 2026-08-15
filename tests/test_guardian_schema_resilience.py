from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = (ROOT / "database.py").read_text(encoding="utf-8")
ADMIN = (ROOT / "blueprints/admin.py").read_text(encoding="utf-8")


def test_guardian_has_live_schema_guard_and_retry():
    assert "def guardian_schema_ready" in DB
    assert "def reset_turso_connection" in DB
    assert "db.guardian_schema_ready(conn)" in ADMIN
    assert "Guardian schema repair retry failed" in ADMIN
    assert "temporarily unavailable" in ADMIN


def test_guardian_columns_are_additive_and_idempotent():
    for col in ("assigned_to", "due_at", "escalated_at", "escalation_reason"):
        assert f'"{col}"' in DB
