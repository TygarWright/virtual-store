from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
checks={
"workflow recovery module":"recover_payment_workflow",
"workflow recovery route":"def admin_workflow_recover",
"workflow recover action":"Recover",
"inventory reconciliation":"def reconcile_inventory_consistency",
"reconciliation invokes inventory":"reconcile_inventory_consistency(conn, run_id=int(run_id))",
"reconciliation stores discrepancies":"reconciliation_items",
}
missing=[]
text='\n'.join(p.read_text(errors='ignore') for p in ROOT.glob('*.py'))+'\n'+(ROOT/'blueprints/admin.py').read_text(errors='ignore')+'\n'+(ROOT/'templates/admin/workflows.html').read_text(errors='ignore')
for name, needle in checks.items():
    if needle not in text: missing.append((name,needle))
if missing: raise SystemExit('Round31 gate failed: '+repr(missing))
print('Round31 mastery gate: PASS')
