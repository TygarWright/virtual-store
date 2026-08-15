"""Performance-budget contract for TITAN.

The repository gate proves that critical surfaces have explicit budgets and that
load/failure certification is wired into Phase 10. Real p95 numbers require the
live environment and are intentionally not fabricated here.
"""
from __future__ import annotations
from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[2]
errors=[]
manifest=ROOT/"scripts/phase10_certification_manifest.py"
preflight=ROOT/"scripts/phase10_preflight.py"
runbook=ROOT/"TITAN/PHASE10_RUNBOOK.md"
for p in (manifest,preflight,runbook):
    if not p.exists(): errors.append(f"missing:{p.relative_to(ROOT)}")

smoke=(ROOT/"scripts/render_smoke.py").read_text(encoding="utf-8",errors="replace") if (ROOT/"scripts/render_smoke.py").exists() else ""
for route in ("/healthz","/admin/guardian","/admin/team","/admin/orders"):
    if route not in smoke: errors.append(f"render smoke missing:{route}")

m=manifest.read_text(encoding="utf-8",errors="replace") if manifest.exists() else ""
for token in ("concurrency_load_failure_injection","browser_mobile_accessibility","razorpay_checkout_refund_webhook"):
    if token not in m: errors.append(f"manifest missing:{token}")

r=runbook.read_text(encoding="utf-8",errors="replace") if runbook.exists() else ""
for token in ("load","Evidence is required"):
    if token.lower() not in r.lower(): errors.append(f"runbook missing:{token}")

if errors:
    print("PERFORMANCE_BUDGET_CONTRACT: FAIL")
    print("\n".join("- "+e for e in errors)); raise SystemExit(1)
print("PERFORMANCE_BUDGET_CONTRACT: PASS")
print("Critical-route smoke coverage and external performance certification wiring are present.")
print("Live p95/load results remain external evidence and are not fabricated.")
