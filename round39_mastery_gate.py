from pathlib import Path
import ast

for name in ["analytics_mastery.py","mastery_services.py","schema_contract.py","admin_api.py"]:
    ast.parse(Path(name).read_text())
print("ROUND39 STATIC GATE: PASS")
