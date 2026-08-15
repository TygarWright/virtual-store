"""Deterministic preflight for TITAN Phase 10 deployment certification.

This is a repository-level gate. It does not claim live certification; instead it
proves that the deployment contract, safety defaults, cron architecture and
runtime smoke-test assets are internally consistent before Render is touched.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []


def read(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        errors.append(f"missing: {rel}")
        return ""
    return p.read_text(encoding="utf-8")


render = read("render.yaml")
cron = read("render-reconciliation-cron.yaml")
ci = read(".github/workflows/ci.yml")
req = read("requirements.txt")
runbook = read("TITAN/PHASE10_RUNBOOK.md")
smoke = read("scripts/render_smoke.py")
concurrency_drill = read("scripts/certification/concurrency_failure_drill.py")
migration_drill = read("scripts/certification/staging_migration_restore_drill.py")

# Deployment contract.
for needle in (
    "healthCheckPath: /healthz",
    "mountPath: /opt/render/project/src/instance",
    "startCommand: gunicorn app:app -c gunicorn.conf.py",
):
    if needle not in render:
        errors.append(f"render.yaml missing deployment contract: {needle}")

# Scheduled reconciliation must execute on the web service's persistent DB via HTTP;
# a cron process must not directly open the web service SQLite file.
for needle in (
    "type: cron",
    "SITE_URL",
    "CRON_SECRET",
    "scripts/run_reconciliation.py",
):
    if needle not in cron:
        errors.append(f"reconciliation cron contract missing: {needle}")
if "/internal/reconciliation" not in cron and "/internal/reconciliation" not in read("scripts/run_reconciliation.py"):
    errors.append("scheduled reconciliation has no protected internal trigger")

# Runtime dependency parity.
for package in ("Flask==", "Werkzeug==", "gunicorn==", "requests=="):
    if package not in req:
        errors.append(f"requirements missing deployment-critical dependency prefix: {package}")
if "python-version: \"3.14\"" not in ci:
    errors.append("CI is not pinned to Render-parity Python 3.14")
if "pip install -r requirements.txt pytest" not in ci:
    errors.append("CI does not install the production requirements before tests")
if "python scripts/render_smoke.py" not in ci:
    errors.append("CI does not execute the Render-parity smoke test")
for label, text in (("concurrency failure drill", concurrency_drill), ("staging migration/restore drill", migration_drill)):
    if "PASS" not in text or "__main__" not in text:
        errors.append(f"{label} certification harness is incomplete")

# Safety defaults.
unsafe_ci = ("OTP_DEV_MODE=true", "ALLOW_TEST_GATEWAY=true", "ALLOW_STORE_TEST_MODE=true")
for token in unsafe_ci:
    if token in ci:
        errors.append(f"unsafe test-mode setting found in CI: {token}")

# Render smoke must exercise both public and admin surfaces and extract CSRF from the login form.
for needle in (
    '"/healthz"',
    '"/admin/login"',
    'name="csrf_token"',
    '"/admin/guardian"',
    '"/admin/team"',
):
    if needle not in smoke:
        errors.append(f"render smoke missing critical assertion: {needle}")

# Certification runbook must retain the non-fabrication rule.
if "Never mark `GO` because the code looks correct. Evidence is required." not in runbook:
    errors.append("Phase 10 runbook lost the evidence-required launch rule")

# Public release must not contain obvious secrets or local databases.
for p in ROOT.rglob("*"):
    if not p.is_file():
        continue
    rel = p.relative_to(ROOT).as_posix()
    if p.suffix in {".db", ".sqlite", ".pyc"}:
        errors.append(f"release artifact present: {rel}")
    if "/__pycache__/" in f"/{rel}/":
        errors.append(f"cache present: {rel}")

if errors:
    print("PHASE 10 PREFLIGHT: FAIL")
    for error in errors:
        print(f"- {error}")
    raise SystemExit(1)

print("PHASE 10 PREFLIGHT: PASS")
print("Deployment contract, runtime parity, scheduled reconciliation architecture, CI smoke coverage, and release hygiene are internally consistent.")
print("Live payments/browser/load/security/DR/rollback remain external certification activities.")
