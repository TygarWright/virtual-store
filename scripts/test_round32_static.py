from pathlib import Path
import ast,re
root=Path(__file__).resolve().parents[1]
for p in root.rglob('*.py'):
    ast.parse(p.read_text(), filename=str(p))
admin=(root/'blueprints/admin.py').read_text()
gov=(root/'governance_service.py').read_text()
obs=(root/'observability_service.py').read_text()
base=(root/'templates/admin/base.html').read_text()
checks={
 'guardian_schema_service':'ensure_guardian_mastery_schema' in gov,
 'guardian_timeline':'exception_timeline' in gov and 'exception_events' in gov,
 'guardian_detectors_route':'admin_guardian_detectors' in admin,
 'observability_policy_service':'ensure_alert_policy_schema' in obs and 'set_alert_policy' in obs,
 'observability_policy_route':'admin_observability_policies' in admin,
 'guardian_detector_nav':'admin_guardian_detectors' in base,
 'observability_policy_nav':'admin_observability_policies' in base,
 'schema_contract_updates':'observability_alert_policies' in (root/'schema_contract.py').read_text(),
}
assert all(checks.values()), [k for k,v in checks.items() if not v]
print('Round 32 static mastery checks: PASS')
