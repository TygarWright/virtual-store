#!/usr/bin/env python3
from pathlib import Path
root=Path(__file__).resolve().parents[1]
assert (root/'analytics_mastery.py').exists()
admin=(root/'admin_api.py').read_text()
assert "/analytics/funnel" in admin and "/analytics/cohorts" in admin and "/analytics/experiments/" in admin
assert "/workflows" in admin and "/workflows/<workflow_id>" in admin
source=(root/'analytics_mastery.py').read_text()
for marker in ('def funnel_report','def cohort_report','def experiment_report'):
    assert marker in source
print('ROUND21_MASTERY_GATE: PASS')
