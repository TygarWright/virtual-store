"""Shared admin role presets and permission constants."""

PRESET_PERMISSIONS = {
    "order_manager": ["orders.view", "orders.edit", "orders.refund", "orders.export", "tickets.create", "team.chat"],
    "catalog_manager": ["products.edit"],
    "support_agent": ["orders.view", "tickets.create", "team.chat"],
    "admin_manager": ["admin.manage", "audit.view", "audit.export", "governance.approve", "tickets.create", "team.chat", "content.manage"],
    "content_manager": ["testimonials.manage", "faqs.manage", "newsletter.view", "content.manage", "team.chat"],
    "finance_manager": ["orders.view", "orders.refund", "analytics.view", "audit.view"],
    "inventory_manager": ["products.edit", "inventory.manage", "orders.view", "analytics.view"],
    "customer_support": ["orders.view", "audit.view", "tickets.create", "team.chat"],
}
