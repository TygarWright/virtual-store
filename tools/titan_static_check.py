from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def src(name):
    return (ROOT / name).read_text(encoding='utf-8')

checks = [
    ('shared limiter', 'limiter = Limiter' in src('extensions.py')),
    ('shared csrf', 'csrf = CSRFProtect()' in src('extensions.py')),
    ('payment provider idempotency', 'X-Refund-Idempotency' in src('payment/gateways.py')),
    ('refund retry discrimination', 'requests_mod.exceptions.Timeout' in src('payment/gateways.py')),
    ('refund concurrency guard', 'ux_order_refunds_open_amount' in src('database.py')),
    ('payment amount verification', 'Payment amount/currency mismatch' in src('blueprints/storefront.py')),
    ('provider contracts', 'class NotificationProvider' in src('providers/contracts.py')),
    ('service container', 'class ServiceContainer' in src('service_container.py')),
    ('shared SQLAlchemy extension', 'db_sql = SQLAlchemy(app)' not in src('app.py')),
    ('migration duplicate fixed', "sa.Column('user_agent'" in src('migrations/versions/572f1204729e_initial_migration.py')),
]
for name, ok in checks:
    print(('PASS' if ok else 'FAIL'), name)
if not all(ok for _, ok in checks):
    raise SystemExit(1)
print('ALL_STATIC_CHECKS_PASS')
