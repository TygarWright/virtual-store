"""Dependency-free guard tests for the Phase 1/2/4 engineering contract.

These tests intentionally inspect source text/AST so CI can catch accidental
regressions even when optional runtime services are unavailable.
"""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def _source(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_shared_security_extensions_exist():
    source = _source("extensions.py")
    assert "limiter = Limiter" in source
    assert "csrf = CSRFProtect()" in source


def test_sensitive_routes_have_explicit_rate_limits():
    storefront = _source("blueprints/storefront.py")
    admin = _source("blueprints/admin.py")
    admin_api = _source("admin_api.py")
    for marker in (
        'def api_verify_payment',
        'def auth_send_otp',
        'def auth_verify_otp',
        'def auth_phone_verify',
        'def razorpay_webhook',
    ):
        idx = storefront.index(marker)
        prefix = storefront[max(0, idx - 180):idx]
        assert "@limiter.limit" in prefix, marker
    assert '@limiter.limit("8 per 5 minutes"' in admin
    assert '@limiter.limit("8 per 5 minutes"' in admin_api


def test_webhook_and_csp_are_explicitly_csrf_exempt():
    source = _source("blueprints/storefront.py")
    assert source.count("@csrf.exempt") >= 2
    assert 'def razorpay_webhook' in source


def test_provider_contracts_and_di_exist():
    contracts = _source("providers/contracts.py")
    defaults = _source("providers/defaults.py")
    container = _source("service_container.py")
    for name in ("NotificationProvider", "AuthProvider", "StorageProvider", "SearchProvider"):
        assert f"class {name}" in contracts
    for name in ("FirebaseAuthProvider", "LocalStorageProvider", "SimpleSearchProvider", "VirtualStoreEmailProvider"):
        assert f"class {name}" in defaults
    assert "class ServiceContainer" in container
    assert 'app.extensions["titan.services"]' in _source("app.py")


def test_sqlalchemy_uses_shared_extension():
    app = _source("app.py")
    models = _source("models.py")
    assert "from extensions import db as sqlalchemy_db" in app
    assert "sqlalchemy_db.init_app(app)" in app
    assert "from extensions import db" in models
    assert "db_sql = SQLAlchemy(app)" not in app


def test_phase2_financial_guards_present():
    gateways = _source("payment/gateways.py")
    refunds = _source("payment/refund.py")
    storefront = _source("blueprints/storefront.py")
    database = _source("database.py")
    assert "X-Refund-Idempotency" in gateways
    assert "RequestsException" not in gateways  # stale broad retry pattern must stay gone
    assert "Timeout, requests_mod.exceptions.ConnectionError" in gateways
    assert "ux_order_refunds_open_amount" in database
    assert "payment amount/currency mismatch" in storefront
    assert "partial unique index" in refunds


def test_migration_has_refund_guard_and_no_duplicate_download_audit_column():
    source = _source("migrations/versions/572f1204729e_initial_migration.py")
    assert "ux_order_refunds_open_amount" in source
    assert source.count("sa.Column('user_agent'") == 1


def test_storefront_auth_only_exposes_configured_providers():
    app = _source("app.py")
    modal = _source("templates/_auth_modal.html")
    js = _source("static/js/auth.js")
    assert "def inject_customer_auth_config" in app
    assert "customer_auth_enabled=(firebase_enabled or google_enabled)" in app
    assert "{% if firebase_auth_enabled %}" in modal
    assert "authStepGoogleOnly" in modal
    assert "authStepGoogleOnly" in js


def test_admin_routes_do_not_generate_double_slashes():
    source = _source("blueprints/admin.py")
    assert '@admin_bp.route("//' not in source


def test_static_assets_have_cache_policy():
    source = _source("app.py")
    assert "stale-while-revalidate=86400" in source
    assert "Cache-Control" in source
