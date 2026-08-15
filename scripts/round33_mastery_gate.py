from pathlib import Path
import ast, sys, subprocess
ROOT=Path(__file__).resolve().parents[1]
for p in ROOT.rglob('*.py'):
    if any(part in {'.git','__pycache__'} for part in p.parts): continue
    try: ast.parse(p.read_text(errors='ignore'))
    except Exception as e: print('PYTHON FAIL', p, e); sys.exit(1)
# Template count and nav/support contract
base=(ROOT/'templates/admin/base.html').read_text()
admin=(ROOT/'blueprints/admin.py').read_text()
checks=[('support route','admin_support_cockpit' in admin),('support nav','admin_support_cockpit' in base),('design audit exists',(ROOT/'scripts/design_system_audit.py').exists())]
for n,ok in checks:
    print(n, 'PASS' if ok else 'FAIL')
    if not ok: sys.exit(1)
print('ROUND33 MASTERY GATE: PASS')
