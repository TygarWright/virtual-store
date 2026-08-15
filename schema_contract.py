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
    ColumnSpec("domain_events", "event_id", "TEXT"),
    ColumnSpec("domain_events", "topic", "TEXT NOT NULL"),
    ColumnSpec("domain_event_deliveries", "event_id", "TEXT NOT NULL"),
    ColumnSpec("domain_event_deliveries", "consumer", "TEXT NOT NULL"),
    ColumnSpec("domain_event_deliveries", "status", "TEXT NOT NULL DEFAULT 'pending'"),
    ColumnSpec("domain_event_deliveries", "attempts", "INTEGER NOT NULL DEFAULT 0"),
    ColumnSpec("domain_event_deliveries", "available_at", "TEXT"),
    ColumnSpec("domain_event_deliveries", "max_attempts", "INTEGER NOT NULL DEFAULT 5"),
    ColumnSpec("domain_event_deliveries", "delivered_at", "TEXT"),
    ColumnSpec("team_messages", "parent_message_id", "INTEGER"),
    ColumnSpec("team_message_reactions", "message_id", "INTEGER NOT NULL"),
    ColumnSpec("approval_steps", "approval_id", "INTEGER NOT NULL"),
    ColumnSpec("approval_steps", "step_index", "INTEGER NOT NULL"),
    ColumnSpec("approval_steps", "status", "TEXT NOT NULL DEFAULT 'pending'"),
    ColumnSpec("admin_approval_requests", "policy_version", "INTEGER NOT NULL DEFAULT 1"),
    ColumnSpec("admin_approval_requests", "policy_snapshot_json", "TEXT NOT NULL DEFAULT '{}'"),
    ColumnSpec("high_risk_action_policies", "version", "INTEGER NOT NULL DEFAULT 1"),
    ColumnSpec("reconciliation_items", "due_at", "TEXT"),
    ColumnSpec("reconciliation_items", "resolution_code", "TEXT NOT NULL DEFAULT ''"),
    ColumnSpec("reconciliation_items", "signed_off_by", "INTEGER"),
    ColumnSpec("reconciliation_items", "signed_off_at", "TEXT"),
    ColumnSpec("payment_events", "event_id", "TEXT"),
    ColumnSpec("workflow_runs", "workflow_id", "TEXT"),
    ColumnSpec("workflow_runs", "status", "TEXT NOT NULL DEFAULT 'pending'"),
    ColumnSpec("workflow_runs", "current_step", "INTEGER NOT NULL DEFAULT 0"),
    ColumnSpec("workflow_runs", "attempt_count", "INTEGER NOT NULL DEFAULT 0"),
    ColumnSpec("workflow_runs", "last_attempt_at", "TEXT"),
    ColumnSpec("workflow_runs", "max_attempts", "INTEGER NOT NULL DEFAULT 3"),
    ColumnSpec("workflow_runs", "compensation_status", "TEXT NOT NULL DEFAULT 'not_needed'"),
    ColumnSpec("workflow_steps", "workflow_id", "TEXT NOT NULL"),
    ColumnSpec("workflow_steps", "step_index", "INTEGER NOT NULL"),
    ColumnSpec("workflow_steps", "status", "TEXT NOT NULL DEFAULT 'pending'"),
    ColumnSpec("reconciliation_runs", "id", "INTEGER"),
    ColumnSpec("reconciliation_locks", "provider", "TEXT"),
    ColumnSpec("reconciliation_locks", "acquired_until", "TEXT NOT NULL"),
    ColumnSpec("reconciliation_runs", "provider", "TEXT NOT NULL DEFAULT 'internal'"),
    ColumnSpec("reconciliation_runs", "status", "TEXT NOT NULL DEFAULT 'completed'"),
    ColumnSpec("reconciliation_items", "id", "INTEGER"),
    ColumnSpec("reconciliation_items", "run_id", "INTEGER NOT NULL"),
    ColumnSpec("reconciliation_items", "code", "TEXT NOT NULL"),
    ColumnSpec("reconciliation_items", "resolved", "INTEGER NOT NULL DEFAULT 0"),
    ColumnSpec("reconciliation_items", "resolution", "TEXT NOT NULL DEFAULT ''"),
    ColumnSpec("reconciliation_items", "resolved_by", "INTEGER"),
    ColumnSpec("reconciliation_items", "resolved_at", "TEXT"),
    ColumnSpec("financial_ledger", "id", "INTEGER"),
    ColumnSpec("financial_ledger", "entry_key", "TEXT NOT NULL"),
    ColumnSpec("financial_ledger", "entry_type", "TEXT NOT NULL"),
    ColumnSpec("financial_ledger", "order_id", "INTEGER"),
    ColumnSpec("financial_ledger", "refund_id", "INTEGER"),
    ColumnSpec("financial_ledger", "amount", "INTEGER NOT NULL"),
    ColumnSpec("financial_ledger", "currency", "TEXT NOT NULL DEFAULT 'INR'"),
    ColumnSpec("financial_ledger", "occurred_at", "TEXT NOT NULL"),
    ColumnSpec("financial_ledger_snapshots", "id", "INTEGER"),
    ColumnSpec("financial_ledger_snapshots", "period_start", "TEXT NOT NULL"),
    ColumnSpec("financial_ledger_snapshots", "period_end", "TEXT NOT NULL"),
    ColumnSpec("financial_ledger_snapshots", "gross_sales", "INTEGER NOT NULL DEFAULT 0"),
    ColumnSpec("financial_ledger_snapshots", "refunds", "INTEGER NOT NULL DEFAULT 0"),
    ColumnSpec("financial_ledger_snapshots", "net_sales", "INTEGER NOT NULL DEFAULT 0"),
    ColumnSpec("financial_ledger_snapshots", "ledger_entries", "INTEGER NOT NULL DEFAULT 0"),
    ColumnSpec("decision_journal", "lesson", "TEXT NOT NULL DEFAULT ''"),
    ColumnSpec("decision_journal", "future_recommendation", "TEXT NOT NULL DEFAULT ''"),
    ColumnSpec("decision_journal", "effectiveness", "TEXT NOT NULL DEFAULT 'unreviewed'"),
    ColumnSpec("decision_journal", "effectiveness_score", "INTEGER"),
    ColumnSpec("decision_journal", "review_due_at", "TEXT"),
    ColumnSpec("decision_review_history", "decision_id", "INTEGER NOT NULL"),
    ColumnSpec("decision_review_history", "effectiveness", "TEXT NOT NULL DEFAULT 'inconclusive'"),
    ColumnSpec("institutional_memory_links", "source_type", "TEXT NOT NULL"),
    ColumnSpec("institutional_memory_links", "source_id", "INTEGER NOT NULL"),
    ColumnSpec("institutional_memory_links", "related_type", "TEXT NOT NULL"),
    ColumnSpec("team_notifications", "id", "INTEGER"),
    ColumnSpec("team_conversations", "context_type", "TEXT NOT NULL DEFAULT ''"),
    ColumnSpec("team_conversations", "context_id", "INTEGER"),
    ColumnSpec("team_message_pins", "message_id", "INTEGER"),
    ColumnSpec("admin_presence", "admin_id", "INTEGER"),
    ColumnSpec("feature_flags", "key", "TEXT NOT NULL"),
    ColumnSpec("experiments", "key", "TEXT NOT NULL"),
    ColumnSpec("experiment_exposure_events", "experiment_id", "INTEGER NOT NULL"),
    ColumnSpec("experiment_guardrail_history", "experiment_id", "INTEGER NOT NULL"),
    ColumnSpec("approval_delegations", "delegate_id", "INTEGER NOT NULL"),
    ColumnSpec("segregation_rules", "action", "TEXT NOT NULL"),
    ColumnSpec("experiments", "conclusion", "TEXT NOT NULL DEFAULT ''"),
    ColumnSpec("experiments", "conclusion_by", "INTEGER"),
    ColumnSpec("experiments", "guardrails_passed", "INTEGER NOT NULL DEFAULT 1"),
    ColumnSpec("experiment_assignments", "experiment_id", "INTEGER NOT NULL"),
    ColumnSpec("experiment_guardrails", "experiment_id", "INTEGER NOT NULL"),
    ColumnSpec("experiment_guardrails", "metric", "TEXT NOT NULL"),
    ColumnSpec("institutional_memory_index", "id", "INTEGER"),
    ColumnSpec("observability_spans", "trace_id", "TEXT NOT NULL"),
    ColumnSpec("observability_spans", "span_id", "TEXT NOT NULL"),
    ColumnSpec("observability_spans", "kind", "TEXT NOT NULL"),
    ColumnSpec("observability_spans", "name", "TEXT NOT NULL"),
    ColumnSpec("observability_spans", "status", "TEXT NOT NULL DEFAULT 'ok'"),
    ColumnSpec("guardian_detectors", "code", "TEXT NOT NULL"),
    ColumnSpec("guardian_detectors", "enabled", "INTEGER NOT NULL DEFAULT 1"),
    ColumnSpec("exception_events", "exception_id", "INTEGER NOT NULL"),
    ColumnSpec("exception_events", "event_type", "TEXT NOT NULL"),
    ColumnSpec("observability_alert_policies", "alert_type", "TEXT NOT NULL"),
    ColumnSpec("observability_alert_policies", "enabled", "INTEGER NOT NULL DEFAULT 1"),
    ColumnSpec("observability_alert_policies", "cooldown_minutes", "INTEGER NOT NULL DEFAULT 10"),
    ColumnSpec("observability_slo_policies", "key", "TEXT NOT NULL"),
    ColumnSpec("observability_slo_policies", "operation_pattern", "TEXT NOT NULL"),
    ColumnSpec("simulation_scenarios", "key", "TEXT"),
    ColumnSpec("simulation_scenarios", "acceptance_json", "TEXT NOT NULL DEFAULT '[]'"),
    ColumnSpec("training_attempts", "admin_id", "INTEGER"),
    ColumnSpec("training_attempts", "scenario_key", "TEXT NOT NULL"),
    ColumnSpec("training_attempts", "score", "INTEGER NOT NULL DEFAULT 0"),
    ColumnSpec("training_attempts", "passed", "INTEGER NOT NULL DEFAULT 0"),
]


# These tables are intentionally created lazily by their owning subsystem.
# Their absence must not prevent the whole application from booting. Once the
# subsystem creates the table, the same contract still validates/repairs its
# required columns.
LAZY_TABLES = {
    "domain_event_deliveries",
    "team_message_reactions",
    "reconciliation_locks",
    "experiment_exposure_events",
    "experiment_guardrail_history",
    "approval_delegations",
    "segregation_rules",
    "guardian_detectors",
    "exception_events",
    "observability_alert_policies",
    "observability_slo_policies",
    "simulation_scenarios",
    "training_attempts",
}


def table_columns(conn, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def missing_columns(conn, specs: Iterable[ColumnSpec] = CRITICAL_COLUMNS) -> list[ColumnSpec]:
    cache: Dict[str, set[str]] = {}
    missing: list[ColumnSpec] = []
    for spec in specs:
        cols = cache.setdefault(spec.table, table_columns(conn, spec.table))
        # Lazy subsystem tables are allowed to be absent during application boot.
        # Their owning service creates them before first use; once present, their
        # required columns remain subject to the contract.
        if not cols and spec.table in LAZY_TABLES:
            continue
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
