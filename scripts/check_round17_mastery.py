#!/usr/bin/env python3
"""Dependency-free static gate for Round 17 backend mastery additions."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
required = [
    ROOT / 'titan_invariants.py',
    ROOT / 'titan_workflows.py',
    ROOT / 'TITAN' / 'ROUND17_MASTERY.md',
]
for path in required:
    assert path.exists(), f'missing {path}'

schema = (ROOT / 'schema_contract.py').read_text()
db = (ROOT / 'database.py').read_text()
refund = (ROOT / 'payment' / 'refund.py').read_text()
workflow = (ROOT / 'titan_workflows.py').read_text()
invariants = (ROOT / 'titan_invariants.py').read_text()

for marker in (
    'workflow_runs', 'workflow_steps', 'assigned_to', 'due_at',
):
    assert marker in schema and marker in db, f'schema marker missing: {marker}'

for marker in ('class DurableWorkflow', 'class WorkflowStep', 'WorkflowError'):
    assert marker in workflow, f'workflow marker missing: {marker}'

for marker in ('InvariantViolation', 'assert_refund_within_paid', 'assert_margin_floor'):
    assert marker in invariants, f'invariant marker missing: {marker}'

assert 'assert_refund_within_paid' in refund, 'refund invariant not wired into refund path'

print('ROUND17_MASTERY_GATE_PASS')
