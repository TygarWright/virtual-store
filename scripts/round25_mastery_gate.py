from pathlib import Path
root=Path(__file__).resolve().parents[1]
checks={
'guardian_assign_route':'/guardian/exception/<int:exception_id>/assign' in (root/'blueprints/admin.py').read_text(),
'guardian_escalation_notifications':'guardian_escalated' in (root/'governance_service.py').read_text(),
'guardian_sla_columns':'due_at' in (root/'database.py').read_text() and 'escalated_at' in (root/'database.py').read_text(),
'observability_alerts':'observability_alerts' in (root/'database.py').read_text() and 'http_5xx' in (root/'app.py').read_text(),
'observability_trace_tree':'trace_summary' in (root/'observability_service.py').read_text(),
'workflow_trace':'trace_id' in (root/'titan_workflows.py').read_text(),
'professional_icons':'emoji' not in (root/'templates/admin/guardian.html').read_text().lower(),
}
for k,v in checks.items():
    print(('PASS' if v else 'FAIL'), k)
assert all(checks.values())
print('ROUND25_MASTERY_GATE: PASS')
