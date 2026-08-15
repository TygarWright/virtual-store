from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
for p in ROOT.rglob('*.py'):
    ast.parse(p.read_text(encoding='utf-8'), filename=str(p))

bk = (ROOT / 'backend_kernel.py').read_text(encoding='utf-8')
assert 'dead_letter_event_deliveries' in bk
assert 'retryable_event_deliveries' in bk
assert 'delay_minutes = min(30' in bk

db = (ROOT / 'database.py').read_text(encoding='utf-8')
assert 'SCHEMA_EXTRA_ROUND43' in db
assert 'def ensure_round43_schema' in db

contract = (ROOT / 'schema_contract.py').read_text(encoding='utf-8')
for col in ['available_at', 'max_attempts', 'delivered_at']:
    assert col in contract

gov = (ROOT / 'governance_service.py').read_text(encoding='utf-8')
assert 'def guardian_health' in gov
admin = (ROOT / 'blueprints/admin.py').read_text(encoding='utf-8')
assert '@admin_bp.route("/guardian/health")' in admin
html = (ROOT / 'templates/admin/guardian.html').read_text(encoding='utf-8')
assert 'guardian-health-title' in html
css = (ROOT / 'static/css/titan-ui.css').read_text(encoding='utf-8')
assert '.guardian-health' in css
print('ROUND43_STATIC_PASS')
