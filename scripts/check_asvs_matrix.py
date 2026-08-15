#!/usr/bin/env python3
from pathlib import Path
import re, sys
ROOT=Path(__file__).resolve().parents[1]
checks=[]
required=["TITAN/OWASP_ASVS_MATRIX.md","scripts/titan_doctor.py","schema_contract.py","backend_kernel.py","titan_workflows.py","helpers.py"]
for p in required: checks.append((p, (ROOT/p).exists()))
text=(ROOT/"helpers.py").read_text()
checks.append(("rate limiting present", "rate_limited" in text))
checks.append(("csrf present", "check_csrf_api" in text))
checks.append(("permission enforcement present", "requires_permission" in text))
checks.append(("idempotency present", "begin_idempotent_operation" in (ROOT/"backend_kernel.py").read_text()))
checks.append(("workflow persistence present", "workflow_runs" in (ROOT/"titan_workflows.py").read_text()))
checks.append(("invariants registry present", (ROOT/"invariant_registry.py").exists()))
checks.append(("security regression suite present", (ROOT/"scripts/security_regression_suite.py").exists()))
failed=[name for name,ok in checks if not ok]
print("TITAN ASVS GATE: PASS" if not failed else "TITAN ASVS GATE: FAIL")
for name,ok in checks: print(("PASS" if ok else "FAIL")+" - "+name)
sys.exit(0 if not failed else 1)
