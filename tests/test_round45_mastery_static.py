from pathlib import Path

ROOT = Path(__file__).parents[1]

def test_approval_policy_versioning_present():
    gov = (ROOT / 'governance_service.py').read_text(encoding='utf-8')
    api = (ROOT / 'admin_api.py').read_text(encoding='utf-8')
    schema = (ROOT / 'database.py').read_text(encoding='utf-8')
    assert 'policy_snapshot_json' in gov
    assert 'policy_version' in gov
    assert 'version=high_risk_action_policies.version+1' in api
    assert 'ensure_round45_schema(conn)' in schema

def test_reconciliation_aging_and_signoff_present():
    recon = (ROOT / 'reconcile_razorpay.py').read_text(encoding='utf-8')
    api = (ROOT / 'admin_api.py').read_text(encoding='utf-8')
    schema = (ROOT / 'schema_contract.py').read_text(encoding='utf-8')
    assert 'def aging_summary' in recon
    assert 'resolution_code' in recon
    assert 'signed_off_by' in recon
    assert '/governance/reconciliation-aging' in api
    assert 'reconciliation_items", "due_at"' in schema
