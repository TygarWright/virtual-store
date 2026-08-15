"""Emit a machine-readable Phase 10 certification manifest.

The manifest separates repository evidence (automated here) from evidence that
must be attached after staging/production execution. It intentionally refuses to
mark external items as passed without a real evidence file.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "PHASE10_CERTIFICATION_MANIFEST.json"
OUT.parent.mkdir(parents=True, exist_ok=True)

repository_checks = [
    ("deployment_contract", ROOT / "render.yaml"),
    ("reconciliation_cron_contract", ROOT / "render-reconciliation-cron.yaml"),
    ("phase10_preflight", ROOT / "scripts/phase10_preflight.py"),
    ("render_parity_smoke", ROOT / "scripts/render_smoke.py"),
    ("phase10_gate", ROOT / "scripts/verify_titan_phase10.py"),
    ("security_gate", ROOT / "scripts/security_regression_suite.py"),
    ("asvs_gate", ROOT / "scripts/asvs_evidence.py"),
    ("disaster_recovery_drill", ROOT / "scripts/disaster_recovery_drill.py"),
    ("staging_migration_restore_drill", ROOT / "scripts/certification/staging_migration_restore_drill.py"),
    ("concurrency_failure_drill", ROOT / "scripts/certification/concurrency_failure_drill.py"),
    ("razorpay_lifecycle_drill", ROOT / "scripts/certification/razorpay_lifecycle_drill.py"),
    ("browser_contract_drill", ROOT / "scripts/certification/browser_contract_drill.py"),
    ("rollback_rehearsal", ROOT / "scripts/certification/rollback_rehearsal.py"),
    ("adversarial_business_logic_drill", ROOT / "scripts/certification/adversarial_business_logic_drill.py"),
    ("production_ops_smoke", ROOT / "scripts/certification/production_ops_smoke.py"),
    ("performance_budget_contract", ROOT / "scripts/certification/performance_budget_contract.py"),
]

repo = []
for name, path in repository_checks:
    repo.append({"name": name, "present": path.exists(), "path": str(path.relative_to(ROOT))})

external = [
    {"name": "razorpay_checkout_refund_webhook", "status": "HARNESS_READY_EXTERNAL_EVIDENCE_REQUIRED", "repository_harness": "scripts/certification/razorpay_lifecycle_drill.py"},
    {"name": "browser_mobile_accessibility", "status": "HARNESS_READY_EXTERNAL_EVIDENCE_REQUIRED", "repository_harness": "scripts/certification/browser_contract_drill.py"},
    {"name": "concurrency_load_failure_injection", "status": "HARNESS_READY_EXTERNAL_EVIDENCE_REQUIRED"},
    {"name": "staging_migration_restore", "status": "HARNESS_READY_EXTERNAL_EVIDENCE_REQUIRED"},
    {"name": "deployment_rollback", "status": "HARNESS_READY_EXTERNAL_EVIDENCE_REQUIRED", "repository_harness": "scripts/certification/rollback_rehearsal.py"},
    {"name": "adversarial_security", "status": "HARNESS_READY_EXTERNAL_EVIDENCE_REQUIRED", "repository_harness": "scripts/certification/adversarial_business_logic_drill.py"},
    {"name": "production_operations", "status": "HARNESS_READY_EXTERNAL_EVIDENCE_REQUIRED", "repository_harness": "scripts/certification/production_ops_smoke.py"},
    {"name": "performance_load", "status": "HARNESS_READY_EXTERNAL_EVIDENCE_REQUIRED", "repository_harness": "scripts/certification/performance_budget_contract.py"},
]

manifest = {
    "schema_version": 1,
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "repository_evidence": repo,
    "external_certification": external,
    "decision": "PENDING_EXTERNAL_EVIDENCE",
}
OUT.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(manifest, indent=2, sort_keys=True))
