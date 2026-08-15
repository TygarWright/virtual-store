#!/usr/bin/env python3
from pathlib import Path
root=Path(__file__).resolve().parents[1]
admin=(root/'blueprints/admin.py').read_text()
gov=(root/'governance_service.py').read_text()
checks={
 'guardian_ack': 'def admin_guardian_acknowledge' in admin,
 'guardian_reopen': 'def admin_guardian_reopen' in admin,
 'guardian_notify': 'def notify_exception_assignee' in gov,
 'reconciliation_ui': (root/'templates/admin/reconciliation.html').exists() and 'def admin_reconciliation' in admin,
 'admin_module_is_python': admin.startswith('from ') or admin.startswith('\"\"\"') or admin.startswith('#'),
}
for k,v in checks.items(): print(f'{k}: {"PASS" if v else "FAIL"}')
raise SystemExit(0 if all(checks.values()) else 1)
