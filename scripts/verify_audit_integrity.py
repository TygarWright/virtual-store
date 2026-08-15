"""Verify the persisted TITAN admin-audit hash chain."""
from __future__ import annotations
import os, sqlite3, sys

DB = os.environ.get("DB_PATH") or "instance/store.db"

def main() -> int:
    if not os.path.exists(DB):
        print(f"SKIP audit chain: database not present at {DB!r}; provide DB_PATH for a deployed/local database")
        return 0
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    from governance_service import verify_audit_integrity
    result = verify_audit_integrity(conn)
    if not result.get("ok"):
        print("FAIL", result)
        return 1
    print(f"PASS audit chain rows={result.get('checked', 0)}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
