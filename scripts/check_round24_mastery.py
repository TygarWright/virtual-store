from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
checks = {
    ROOT / 'commerce_workflows.py': ['confirm_order_payment_durable'],
    ROOT / 'blueprints/storefront.py': ['confirm_order_payment_durable'],
    ROOT / 'scripts/run_reconciliation.py': ['/internal/reconciliation', 'SITE_URL', 'CRON_SECRET'],
    ROOT / 'render-reconciliation-cron.yaml': ['type: cron', 'schedule: "0 2 * * *"'],
    ROOT / 'reconcile_razorpay.py': ['def resolve_item', 'def get_open_items'],
    ROOT / 'blueprints/admin.py': ['def admin_reconciliation_resolve', 'def admin_workflows'],
    ROOT / 'database.py': ['ensure_round24_schema'],
    ROOT / 'templates/admin/workflows.html': ['Commerce Workflows'],
    ROOT / 'blueprints/health.py': ['def internal_reconciliation_trigger'],
    ROOT / 'schema_contract.py': ['ColumnSpec("reconciliation_items", "resolution"'],
}
missing=[]
for path, needles in checks.items():
    if not path.exists():
        missing.append((str(path), 'file missing'))
        continue
    text=path.read_text(errors='ignore')
    for needle in needles:
        if needle not in text:
            missing.append((str(path), needle))
if missing:
    raise SystemExit('Round24 gate failed: '+repr(missing))
print('Round24 mastery gate: PASS')
