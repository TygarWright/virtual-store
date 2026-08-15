#!/usr/bin/env python3
"""Final Phase 10 gate.

GO is impossible until every required external certification item has a real,
hash-verified evidence artifact and the repository gates are clean.
"""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "reports" / "PHASE10_EXTERNAL_EVIDENCE.json"
REQUIRED = [
    "razorpay_checkout_refund_webhook",
    "browser_mobile_accessibility",
    "concurrency_load_failure_injection",
    "staging_migration_restore",
    "deployment_rollback",
    "adversarial_security",
    "production_operations",
    "performance_load",
]


def run(name: str, script: str) -> bool:
    p = subprocess.run([sys.executable, str(ROOT / script)], cwd=ROOT, capture_output=True, text=True)
    print(f"[{name}] {'PASS' if p.returncode == 0 else 'FAIL'}")
    if p.stdout.strip(): print(p.stdout.strip())
    if p.returncode != 0 and p.stderr.strip(): print(p.stderr.strip(), file=sys.stderr)
    return p.returncode == 0


def main() -> int:
    repo_checks = [
        ("phase10_preflight", "scripts/phase10_preflight.py"),
        ("phase10_static_gate", "scripts/verify_titan_phase10.py"),
        ("security_regression", "scripts/security_regression_suite.py"),
        ("asvs_evidence", "scripts/asvs_evidence.py"),
    ]
    ok = all(run(*x) for x in repo_checks)
    if not REG.exists():
        print("FINAL_PHASE10_GATE: NO-GO (external evidence registry missing)")
        return 1
    data = json.loads(REG.read_text(encoding="utf-8"))
    items = data.get("items", {})
    missing = [x for x in REQUIRED if items.get(x, {}).get("status") != "VERIFIED"]
    if missing:
        ok = False
        print("Missing verified external evidence:")
        for x in missing: print(f"- {x}")
    print("FINAL_PHASE10_GATE: " + ("GO" if ok else "NO-GO"))
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
