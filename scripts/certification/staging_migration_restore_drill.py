"""Reproducible staging migration/restore rehearsal for TITAN.

This deliberately uses only Python's stdlib so the certification harness can run
before the full application dependency stack is installed. It extracts the
canonical database SCHEMA/MIGRATIONS from database.py, creates a representative
legacy database, restores it into a clean target, applies the real migration
sequence with the same duplicate-tolerant semantics, and verifies business
markers plus schema fingerprints.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_SOURCE = ROOT / "database.py"


def _extract_assignments():
    tree = ast.parse(DB_SOURCE.read_text(encoding="utf-8"), filename=str(DB_SOURCE))
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id == "SCHEMA" or node.targets[0].id == "MIGRATIONS" or node.targets[0].id.startswith("SCHEMA_EXTRA"):
                values[node.targets[0].id] = ast.literal_eval(node.value)
    if "SCHEMA" not in values or "MIGRATIONS" not in values:
        raise RuntimeError("Could not extract canonical database SCHEMA/MIGRATIONS")
    return values


def _exec_schema(conn, schema: str):
    conn.executescript(schema)
    conn.commit()


def _exec_missing_tables(conn, schema_extra: str):
    # SCHEMA_EXTRA contains some objects that are intentionally introduced by
    # later migrations. Recreate only its CREATE TABLE statements so the legacy
    # fixture has the tables required for subsequent ALTER/CREATE INDEX steps
    # without accidentally importing the latest columns.
    for statement in schema_extra.split(";"):
        stmt = statement.strip()
        if re.match(r"^CREATE TABLE IF NOT EXISTS\s+", stmt, re.I):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as exc:
                if "already exists" not in str(exc).lower():
                    raise
    conn.commit()


def _migration_id(stmt: str) -> str:
    return hashlib.sha1(stmt.encode("utf-8")).hexdigest()[:16]


def _apply_real_migrations(conn, migrations):
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    applied = {row[0] for row in conn.execute("SELECT id FROM schema_migrations").fetchall()}
    skipped_duplicate = 0
    applied_now = 0
    for stmt in migrations:
        mid = _migration_id(stmt)
        if mid in applied:
            continue
        try:
            conn.execute(stmt)
            applied_now += 1
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if not any(x in msg for x in ("duplicate column", "already exists", "duplicate index", "cannot add a unique column")):
                raise
            skipped_duplicate += 1
        conn.execute("INSERT OR IGNORE INTO schema_migrations(id, applied_at) VALUES(?, datetime('now'))", (mid,))
    conn.commit()
    return applied_now, skipped_duplicate


def _fingerprint(conn):
    tables = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    indexes = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL ORDER BY name").fetchall()
    payload = {"tables": [tuple(r) for r in tables], "indexes": [tuple(r) for r in indexes]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _marker_fingerprint(conn):
    marker_tables = ["products", "orders", "customers", "financial_ledger", "domain_events", "idempotency_keys"]
    out = {}
    for table in marker_tables:
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            count = None
        out[table] = count
    return out


def main():
    values = _extract_assignments()
    with tempfile.TemporaryDirectory(prefix="titan-phase10-migration-") as td:
        root = Path(td)
        source = root / "staging-source.sqlite"
        restored = root / "staging-restored.sqlite"

        src = sqlite3.connect(source)
        src.execute("PRAGMA foreign_keys=ON")
        _exec_schema(src, values["SCHEMA"])
        for name, extra in values.items():
            if name.startswith("SCHEMA_EXTRA") and isinstance(extra, str):
                _exec_missing_tables(src, extra)
        # Representative business data that must survive backup/restore.
        src.execute("INSERT INTO products(name, slug, price, category, active, position, created_at) VALUES(?,?,?,?,?,?,datetime('now'))", ("CERT PRODUCT", "cert-product", 1999, "cert", 1, 1))
        product_id = src.execute("SELECT id FROM products WHERE slug='cert-product'").fetchone()[0]
        src.execute("INSERT INTO customers(name, email, phone, created_at) VALUES(?,?,?,datetime('now'))", ("Certification Customer", "cert@example.invalid", "+910000000000"))
        customer_id = src.execute("SELECT id FROM customers WHERE email='cert@example.invalid'").fetchone()[0]
        src.execute("INSERT INTO orders(order_ref, product_id, product_name, customer_name, customer_email, amount, created_at) VALUES(?,?,?,?,?,?,datetime('now'))", ("CERT-RESTORE-001", product_id, "CERT PRODUCT", "Certification Customer", "cert@example.invalid", 1999))
        src.commit()
        before = _marker_fingerprint(src)
        backup_bytes = source.read_bytes()
        src.close()

        restored.write_bytes(backup_bytes)
        dst = sqlite3.connect(restored)
        dst.execute("PRAGMA foreign_keys=ON")
        restored_before = _marker_fingerprint(dst)
        schema_before = _fingerprint(dst)
        applied, skipped = _apply_real_migrations(dst, values["MIGRATIONS"])
        # Re-run the exact migration list: this proves ledger idempotency on the restored copy.
        applied2, skipped2 = _apply_real_migrations(dst, values["MIGRATIONS"])
        after = _marker_fingerprint(dst)
        schema_after = _fingerprint(dst)
        integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        fk_violations = dst.execute("PRAGMA foreign_key_check").fetchall()
        marker = dst.execute("SELECT order_ref FROM orders WHERE order_ref='CERT-RESTORE-001'").fetchone()
        dst.close()

    assert restored_before == before, (before, restored_before)
    assert after == before, (before, after)
    assert marker and marker[0] == "CERT-RESTORE-001"
    assert integrity == "ok", integrity
    assert not fk_violations, fk_violations
    assert applied2 == 0, applied2
    assert skipped2 == 0, skipped2
    assert schema_before != schema_after or applied == 0 or skipped > 0

    print("STAGING_MIGRATION_RESTORE_DRILL: PASS")
    print(f"business_marker_counts={json.dumps(before, sort_keys=True)}")
    print(f"migrations_applied={applied} duplicate_safe_skips={skipped}")
    print(f"migration_rerun_applied={applied2} migration_rerun_skips={skipped2}")
    print(f"integrity_check={integrity} foreign_key_violations=0")
    print(f"schema_before={schema_before} schema_after={schema_after}")


if __name__ == "__main__":
    main()
