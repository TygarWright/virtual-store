"""Phase 10 schema-boot contract.

Proves the startup schema guard does not require lazily-created subsystem tables
before those subsystems are first used, while still repairing missing columns
once a lazy table physically exists.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from schema_contract import CRITICAL_COLUMNS, LAZY_TABLES, missing_columns, repair_missing_columns  # noqa: E402


def main() -> int:
    fd, db_path = tempfile.mkstemp(prefix="titan-schema-boot-", suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(db_path)
    try:
        by_table: dict[str, list] = {}
        for spec in CRITICAL_COLUMNS:
            if spec.table in LAZY_TABLES:
                continue
            by_table.setdefault(spec.table, []).append(spec)
        for table, specs in by_table.items():
            cols = [f'"{s.name}" {s.definition}' for s in specs]
            conn.execute(f'CREATE TABLE "{table}" ({", ".join(cols)})')
        conn.commit()

        repair_missing_columns(conn)
        remaining = missing_columns(conn)
        if remaining:
            names = ", ".join(f"{x.table}.{x.name}" for x in remaining)
            raise AssertionError(f"unexpected core schema gaps: {names}")

        # Lazy tables are intentionally absent in this fixture; if they were
        # treated as mandatory, repair_missing_columns() above would raise.

        # Lazy table present but incomplete: contract must repair it.
        conn.execute("CREATE TABLE team_message_reactions (id INTEGER)")
        repaired = repair_missing_columns(conn)
        repaired_names = {(x.table, x.name) for x in repaired}
        if ("team_message_reactions", "message_id") not in repaired_names:
            raise AssertionError("lazy-table column repair did not run")

        print("SCHEMA_BOOT_CONTRACT: PASS")
        print("LAZY_TABLE_BOOT_OPTIONAL: PASS")
        print("LAZY_TABLE_COLUMN_REPAIR: PASS")
        return 0
    finally:
        conn.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
