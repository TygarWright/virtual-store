#!/usr/bin/env python3
"""Human-friendly production readiness doctor for Virtual Store TITAN."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_TABLES = [
    "products", "orders", "customers", "admin_users", "settings",
    "business_exceptions", "outbox_jobs", "idempotency_keys",
]


def _db_path() -> Path | None:
    raw = os.getenv("DATABASE_PATH") or os.getenv("SQLITE_PATH")
    if raw:
        return Path(raw)
    candidate = ROOT / "instance" / "store.db"
    return candidate if candidate.exists() else None


def main() -> int:
    checks = []
    debug_enabled = os.getenv("DEBUG", "false").lower() == "true"
    checks.append({"name": "production_debug_disabled", "ok": not debug_enabled})
    checks.append({"name": "secret_key_present", "ok": bool(os.getenv("SECRET_KEY"))})
    checks.append({"name": "razorpay_configured", "ok": bool(os.getenv("RAZORPAY_KEY_ID") and os.getenv("RAZORPAY_KEY_SECRET"))})
    checks.append({"name": "webhook_secret_configured", "ok": bool(os.getenv("RAZORPAY_WEBHOOK_SECRET"))})
    checks.append({"name": "store_test_mode_disabled", "ok": os.getenv("ALLOW_STORE_TEST_MODE", "false").lower() != "true"})
    checks.append({"name": "otp_dev_mode_disabled", "ok": os.getenv("OTP_DEV_MODE", "false").lower() != "true"})
    checks.append({"name": "csrf_enabled", "ok": os.getenv("CSRF_ENABLED", "true").lower() in ("true", "1", "yes", "on")})

    db = _db_path()
    if db:
        try:
            conn = sqlite3.connect(db)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = [t for t in REQUIRED_TABLES if t not in tables]
            checks.append({"name": "database_core_schema", "ok": not missing, "missing": missing})
            conn.close()
        except Exception as exc:
            checks.append({"name": "database_open", "ok": False, "error": str(exc)[:200]})
    else:
        checks.append({"name": "database_local_presence", "ok": True, "detail": "No local database; expected on remote/Turso deployment."})

    result = {"status": "pass" if all(c["ok"] for c in checks) else "attention", "checks": checks}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
