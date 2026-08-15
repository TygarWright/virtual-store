#!/usr/bin/env python3
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from invariant_registry import verify_all
results = verify_all()
failed = [r for r in results if r["status"] != "PASS"]
print(json.dumps({"invariants": results, "passed": len(results)-len(failed), "failed": len(failed)}, indent=2))
sys.exit(0 if not failed else 1)
