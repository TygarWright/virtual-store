#!/usr/bin/env python3
"""Safe disaster-recovery rehearsal for SQLite/Turso-compatible local copies.

Never operates on the live DB in-place. It creates a temporary snapshot,
restores it into a separate temporary destination, and compares structural and
row-count fingerprints.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from titan_db_tools import backup, restore, verify


def fingerprint(path: Path) -> dict:
    with sqlite3.connect(path) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        schema = [r for r in conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name")]
    raw = json.dumps({"tables": tables, "counts": counts, "schema": schema}, sort_keys=True, default=str).encode()
    return {"tables": tables, "counts": counts, "sha256": hashlib.sha256(raw).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Rehearse backup and restore without touching the live DB")
    parser.add_argument("database", nargs="?", help="source SQLite database; if omitted, a disposable representative fixture is created")
    args = parser.parse_args()
    fixture_dir = None
    if args.database:
        source = Path(args.database).resolve()
        if not source.exists():
            raise SystemExit(f"Database does not exist: {source}")
    else:
        fixture_dir = tempfile.TemporaryDirectory(prefix="titan-dr-source-")
        source = Path(fixture_dir.name) / "source.db"
        with sqlite3.connect(source) as conn:
            conn.executescript('''
                PRAGMA foreign_keys=ON;
                CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id), total INTEGER NOT NULL);
                CREATE TABLE audit_log(id INTEGER PRIMARY KEY, action TEXT NOT NULL, created_at TEXT NOT NULL);
                INSERT INTO customers VALUES (1, 'DR fixture');
                INSERT INTO orders VALUES (1, 1, 4280);
                INSERT INTO audit_log VALUES (1, 'fixture.created', '2026-01-01T00:00:00Z');
            ''')
    try:
        ok, problems = verify(str(source))
        if not ok:
            raise SystemExit("Source DB failed verification: " + "; ".join(problems))

        with tempfile.TemporaryDirectory(prefix="titan-drill-") as td:
            root = Path(td)
            backup_path = root / "snapshot.db"
            restore_path = root / "restored.db"
            started = time.monotonic()
            backup(str(source), str(backup_path))
            backup_seconds = time.monotonic() - started
            started = time.monotonic()
            restore(str(backup_path), str(restore_path), force=True)
            restore_seconds = time.monotonic() - started
            src_fp = fingerprint(source)
            restored_fp = fingerprint(restore_path)
            if src_fp != restored_fp:
                print(json.dumps({"status": "FAIL", "source": src_fp, "restored": restored_fp}, indent=2))
                return 1
            # Prove the restore is independently usable and the manifest remains valid.
            ok, problems = verify(str(restore_path))
            if not ok:
                print(json.dumps({"status": "FAIL", "restore_verify": problems}, indent=2))
                return 1
            print(json.dumps({"status": "PASS", "message": "backup → restore → integrity → fingerprint verification succeeded", "fingerprint": src_fp, "source": str(source), "backup_seconds": round(backup_seconds, 4), "restore_seconds": round(restore_seconds, 4), "rpo_note": "snapshot-based drill; production RPO depends on backup schedule", "rto_seconds": round(restore_seconds, 4)}, indent=2))
            return 0
    finally:
        if fixture_dir is not None:
            fixture_dir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
