from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_admin_dashboard_has_actionable_operational_alerts():
    text = (ROOT / "templates/admin/dashboard.html").read_text(encoding="utf-8")
    assert "Store alerts" in text
    assert "Stock needs attention" in text
    assert "Orders need attention" in text


def test_product_schema_uses_configured_currency_and_unlimited_stock():
    text = (ROOT / "templates/product.html").read_text(encoding="utf-8")
    assert "product.quantity is none" in text
    assert "settings.currency_code" in text
    assert "schema.org/InStock" in text


def test_product_share_button_is_explicit_button():
    text = (ROOT / "templates/product.html").read_text(encoding="utf-8")
    assert '<button type="button" class="product-share-overlay"' in text

# --- TITAN Phase 7/8 deep quality checks ---
def test_admin_customer_console_exists_and_is_read_only():
    from pathlib import Path
    src = Path("blueprints/admin.py").read_text()
    tpl = Path("templates/admin/customers.html").read_text()
    start = src.find('def admin_customers')
    block = src[start:start + 7000]
    assert start >= 0
    assert 'requires_permission("orders.view")' in src[start-250:start+100]
    assert 'INSERT' not in block and 'UPDATE ' not in block and 'DELETE ' not in block
    assert 'lifetime_value' in block
    assert 'Search name, email or phone' in tpl


def test_admin_permission_template_helper_is_injected():
    from pathlib import Path
    app = Path("app.py").read_text()
    assert 'def inject_admin_permissions' in app
    assert 'admin_can=lambda' in app
    assert 'has_permission(perms, *required)' in app


def test_catalog_is_paginated_after_filtering_and_sorting():
    from pathlib import Path
    src = Path("blueprints/storefront.py").read_text()
    assert 'per_page = 24' in src
    assert 'total_products = len(products)' in src
    assert 'page_products = products[start:start + per_page]' in src
    assert 'products=page_products' in src


def test_catalog_pagination_preserves_query_parameters():
    from pathlib import Path
    tpl = Path("templates/index.html").read_text()
    assert 'dict(pagination_params, page=page-1)' in tpl
    assert 'dict(pagination_params, page=page+1)' in tpl
    assert 'aria-label="Catalogue pages"' in tpl
