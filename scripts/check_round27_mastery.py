#!/usr/bin/env python3
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
checks = {
    "ASVS evidence generator": (ROOT/"scripts/asvs_evidence.py").exists(),
    "ASVS evidence report directory": (ROOT/"reports").exists(),
    "disaster recovery drill": (ROOT/"scripts/disaster_recovery_drill.py").exists(),
    "backup/restore engine": (ROOT/"titan_db_tools.py").exists(),
    "backup verifies before copy": "Refusing to back up an invalid database" in (ROOT/"titan_db_tools.py").read_text(),
    "restore verifies after copy": "Restored copy failed verification" in (ROOT/"titan_db_tools.py").read_text(),
    "ASVS matrix remains explicit": (ROOT/"TITAN/OWASP_ASVS_MATRIX.md").exists(),
    "invariant registry": (ROOT/"invariant_registry.py").exists(),
    "security regression suite": (ROOT/"scripts/security_regression_suite.py").exists(),
}
failed = [k for k,v in checks.items() if not v]
print("TITAN ROUND 27 GATE: PASS" if not failed else "TITAN ROUND 27 GATE: FAIL")
for k,v in checks.items(): print(("PASS" if v else "FAIL") + " - " + k)
sys.exit(0 if not failed else 1)
