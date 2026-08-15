from pathlib import Path
root=Path(__file__).resolve().parents[1]
assert 'CREATE TABLE IF NOT EXISTS financial_ledger' in (root/'database.py').read_text()
assert 'financial_ledger_snapshots' in (root/'schema_contract.py').read_text()
s=(root/'reconcile_razorpay.py').read_text()
assert 'def _ledger_entry' in s and 'def refresh_ledger_snapshot' in s and 'entry_type="sale"' in s and 'entry_type="refund"' in s
r=(root/'payment/refund.py').read_text()
assert 'def _process_refund_impl' in r and 'workflow_type="order.refund"' in r
assert '/governance/ledger' in (root/'admin_api.py').read_text()
print('Round37 static PD checks: PASS')
