"""Authoritative, runtime-checkable database contract for TITAN.

The live database may be SQLite or Turso/libSQL. We use portable PRAGMA/table
probes rather than trusting migration bookkeeping alone. The contract is
deliberately limited to critical tables/columns whose absence would cause
runtime failures in core flows.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable


@dataclass(frozen=True)
class ColumnSpec:
    table: str
    name: str
    definition: str


CRITICAL_COLUMNS = [
    ColumnSpec("settings", "key", "TEXT"),
    ColumnSpec("products", "id", "INTEGER"),
    ColumnSpec("products", "quantity", "INTEGER NOT NULL DEFAULT 0"),
    ColumnSpec("products", "cost_price", "INTEGER NOT NULL DEFAULT 0"),
    ColumnSpec("products", "min_margin_percent", "INTEGER NOT NULL DEFAULT 15"),
    ColumnSpec("orders", "id", "INTEGER"),
    ColumnSpec("orders", "payment_state", "TEXT NOT NULL DEFAULT 'pending'"),
    ColumnSpec("orders", "order_state", "TEXT NOT NULL DEFAULT 'created'"),
    ColumnSpec("orders", "amount", "INTEGER NOT NULL DEFAULT 0"),
    ColumnSpec("admin_users", "id", "INTEGER"),
    ColumnSpec("admin_users", "role", "TEXT NOT NULL DEFAULT 'custom'"),
    ColumnSpec("admin_users", "permissions", "TEXT NOT NULL DEFAULT '[]'"),
    ColumnSpec("business_exceptions", "id", "INTEGER"),
    ColumnSpec("business_exceptions", "code", "TEXT NOT NULL DEFAULT ''"),
    ColumnSpec("business_exceptions", "severity", "TEXT NOT NULL DEFAULT 'medium'"),
    ColumnSpec("business_exceptions", "title", "TEXT NOT NULL DEFAULT ''"),
    ColumnSpec("business_exceptions", "description", "TEXT NOT NULL DEFAULT ''"),
    ColumnSpec("business_exceptions", "entity", "TEXT NOT NULL DEFAULT ''"),
    ColumnSpec("business_exceptions", "entity_id", "INTEGER NOT NULL DEFAULT 0"),
    ColumnSpec("business_exceptions", "metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
    ColumnSpec("business_exceptions", "status", "TEXT NOT NULL DEFAULT 'open'"),
    ColumnSpec("business_exceptions", "resolved_by", "INTEGER"),
    ColumnSpec("business_exceptions", "resolution", "TEXT NOT NULL DEFAULT ''"),
    ColumnSpec("business_exceptions", "created_at", "TEXT NOT NULL DEFAULT ''"),
    ColumnSpec("business_exceptions", "resolved_at", "TEXT"),
    ColumnSpec("business_exceptions", "assigned_to", "INTEGER"),
    ColumnSpec("business_exceptions", "due_at", "TEXT"),
    ColumnSpec("business_exceptions", "escalated_at", "TEXT"),
    ColumnSpec("business_exceptions", "escalation_reason", "TEXT NOT NULL DEFAULT ''"),
    ColumnSpec("idempotency_keys", "namespace", "TEXT NOT NULL"),
    ColumnSpec("idempotency_keys", "idempotency_key", "TEXT NOT NULL"),
    ColumnSpec("outbox_jobs", "id", "INTEGER"),
    ColumnSpec("outbox_jobs", "status", "TEXT NOT NULL DEFAULT 'pending'"),
    ColumnSpec("payment_events", "event_id", "TEXT"),
]


def table_columns(conn, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def missing_columns(conn, specs: Iterable[ColumnSpec] = CRITICAL_COLUMNS) -> list[ColumnSpec]:
    cache: Dict[str, set[str]] = {}
    missing: list[ColumnSpec] = []
    for spec in specs:
        cols = cache.setdefault(spec.table, table_columns(conn, spec.table))
        if spec.name not in cols:
            missing.append(spec)
    return missing


def repair_missing_columns(conn, specs: Iterable[ColumnSpec] = CRITICAL_COLUMNS) -> list[ColumnSpec]:
    """Add only missing additive columns, then re-check them."""
    repaired: list[ColumnSpec] = []
    by_table: Dict[str, list[ColumnSpec]] = {}
    for spec in missing_columns(conn, specs):
        by_table.setdefault(spec.table, []).append(spec)
    for table, items in by_table.items():
        # If the table itself is absent, leave creation to the canonical schema/migrations.
        existing = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if not existing:
            continue
        for spec in items:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {spec.name} {spec.definition}")
                repaired.append(spec)
            except Exception as exc:
                message = str(exc).lower()
                if "duplicate column" not in message and "already exists" not in message:
                    raise
    if repaired:
        conn.commit()
    still_missing = missing_columns(conn, specs)
    if still_missing:
        names = ", ".join(f"{s.table}.{s.name}" for s in still_missing)
        raise RuntimeError(f"Database schema contract incomplete: {names}")
    return repaired


def assert_schema_contract(conn) -> None:
    missing = missing_columns(conn)
    if missing:
        names = ", ".join(f"{s.table}.{s.name}" for s in missing)
        raise RuntimeError(f"Database schema contract incomplete: {names}")
