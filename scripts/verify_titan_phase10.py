"""Environment-independent Phase 10 release gate.

This gate verifies that the repository is structurally ready for live/staging
certification. It deliberately does not pretend to execute real payment,
DNS/TLS, browser, load, or production rollback tests.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []

# Required certification assets.
required = [
    Path(".github/workflows/ci.yml"),
    Path("render.yaml"),
    Path("requirements.txt"),
    Path("scripts/verify_titan_pre9.py"),
    Path("scripts/verify_titan_phase9.py"),
    Path("backup_db.sh"),
    Path("TITAN/CHECKLIST.md"),
    Path("TITAN/PHASE10_RUNBOOK.md"),
]
for rel in required:
    if not (ROOT / rel).exists():
        errors.append(f"missing required certification asset: {rel}")

ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8") if (ROOT / ".github/workflows/ci.yml").exists() else ""
for forbidden in ("OTP_DEV_MODE=true", "ALLOW_TEST_GATEWAY=true"):
    if forbidden in ci:
        errors.append(f"unsafe CI production-validation setting found: {forbidden}")

ui_gate = ROOT / "scripts/check_ui_quality.py"
if not ui_gate.exists():
    errors.append("missing UI quality gate: scripts/check_ui_quality.py")

config = (ROOT / "config.py").read_text(encoding="utf-8")
for needle in (
    'SECRET_KEY is required',
    'OTP_DEV_MODE must be false when DEBUG=false',
    'ALLOW_TEST_GATEWAY must not be enabled when DEBUG=false',
):
    if needle not in config:
        errors.append(f"missing production guard in config.py: {needle}")

# Publishable tree must not contain obvious local-only artifacts.
for p in ROOT.rglob("*"):
    if not p.is_file():
        continue
    rel = p.relative_to(ROOT).as_posix()
    if p.suffix in {".pyc", ".db", ".sqlite"}:
        errors.append(f"local/generated artifact present: {rel}")
    if "/__pycache__/" in f"/{rel}/":
        errors.append(f"python cache directory present: {rel}")
    if p.name.endswith(".backup") or p.name.endswith(".bak"):
        errors.append(f"backup/development artifact present: {rel}")
    if "PASSWORD" in p.name.upper() and p.name not in {"PASSWORD_POLICY.md"}:
        errors.append(f"password artifact present: {rel}")

if errors:
    print("PHASE 10 STATIC GATE: FAIL")
    for e in errors:
        print(f"- {e}")
    sys.exit(1)

print("PHASE 10 STATIC GATE: PASS")
print("Live/staging certification remains explicitly required for: payments, webhooks, browser/device UX, load, migration/restore rehearsal, deployment and rollback.")
