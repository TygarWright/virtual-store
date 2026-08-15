import importlib.util, sqlite3, sys, types
from pathlib import Path

fake_db = types.SimpleNamespace()
sys.modules['database'] = fake_db
spec = importlib.util.spec_from_file_location('governance_service', Path(__file__).parents[1] / 'governance_service.py')
svc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(svc)


def db():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''
    CREATE TABLE high_risk_action_policies (action TEXT PRIMARY KEY, threshold_amount INTEGER, require_two_person INTEGER, approval_expiry_minutes INTEGER, enabled INTEGER, updated_at TEXT);
    CREATE TABLE admin_approval_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, request_ref TEXT, requested_by INTEGER, action TEXT, entity TEXT, entity_id INTEGER, amount INTEGER, reason TEXT, metadata_json TEXT, status TEXT, approved_by INTEGER, approval_note TEXT, created_at TEXT, approved_at TEXT, updated_at TEXT);
    CREATE TABLE business_exceptions (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, severity TEXT, title TEXT, description TEXT, entity TEXT, entity_id INTEGER, metadata_json TEXT, status TEXT, resolved_by INTEGER, resolution TEXT, created_at TEXT, resolved_at TEXT, updated_at TEXT, assigned_to INTEGER, due_at TEXT, escalated_at TEXT, escalation_reason TEXT);
    CREATE TABLE audit_integrity (id INTEGER PRIMARY KEY, last_hash TEXT);
    INSERT INTO audit_integrity VALUES (1,'');
    ''')
    return c


def test_two_person_approval():
    c=db(); c.execute("INSERT INTO high_risk_action_policies VALUES (?,?,?,?,?,?)",('order.refund',5000,1,1440,1,svc._now()))
    r=svc.request_or_validate_approval(c, action='order.refund', requested_by=1, entity='order', entity_id=7, amount=9000, metadata={'refund_amount':9000})
    assert r['allowed'] is False
    assert svc.approve(c, r['approval_id'], approved_by=1) is False
    assert svc.approve(c, r['approval_id'], approved_by=2) is True
    ok=svc.request_or_validate_approval(c, action='order.refund', requested_by=1, entity='order', entity_id=7, amount=9000, metadata={'refund_amount':9000}, approval_id=r['approval_id'])
    assert ok['allowed'] is True


def test_margin_guard():
    r=svc.coupon_discount_with_margin(price=1000, cost_price=700, discount_type='percent', discount_value=50, min_margin_percent=15)
    assert r['final_price'] == 805
    assert r['discount'] == 195


def test_guardian_escalation():
    c=db(); exc=svc.open_exception(c, code='x', severity='high', title='Problem', description='bad', entity='order', entity_id=1, due_at='2000-01-01T00:00:00+00:00')
    assert svc.escalate_overdue_exceptions(c, now_iso='2026-01-01T00:00:00+00:00') == 1
    row=c.execute('SELECT severity, escalated_at FROM business_exceptions WHERE id=?',(exc,)).fetchone()
    assert row['severity']=='critical' and row['escalated_at']
