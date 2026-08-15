"""Add institutional wisdom control tables.

Revision ID: 8b12c7d4e9f0
Revises: 0f4c2d9ab7e1
"""
from alembic import op

revision = '8b12c7d4e9f0'
down_revision = '0f4c2d9ab7e1'
branch_labels = None
depends_on = None


def upgrade():
    # The SQLite schema bootstrap is authoritative for new installs; these
    # CREATE TABLE IF NOT EXISTS statements make existing deployments forward compatible.
    op.execute('''CREATE TABLE IF NOT EXISTS admin_permission_grants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER NOT NULL,
        permission TEXT NOT NULL,
        granted_by INTEGER,
        expires_at TEXT,
        reason TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        revoked_at TEXT
    )''')
    op.execute('''CREATE TABLE IF NOT EXISTS high_risk_action_policies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT NOT NULL UNIQUE,
        threshold_amount INTEGER NOT NULL DEFAULT 0,
        require_two_person INTEGER NOT NULL DEFAULT 1,
        approval_expiry_minutes INTEGER NOT NULL DEFAULT 1440,
        enabled INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL
    )''')
    op.execute('''CREATE TABLE IF NOT EXISTS support_interactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        admin_id INTEGER,
        channel TEXT NOT NULL DEFAULT 'internal',
        subject TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL,
        outcome TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )''')
    op.execute('''CREATE TABLE IF NOT EXISTS audit_integrity (
        id INTEGER PRIMARY KEY CHECK(id=1),
        last_hash TEXT NOT NULL DEFAULT ''
    )''')
    op.execute("INSERT OR IGNORE INTO audit_integrity(id,last_hash) VALUES (1,'')")
    op.execute('''CREATE TABLE IF NOT EXISTS inventory_controls (
        product_id INTEGER PRIMARY KEY,
        safety_stock INTEGER NOT NULL DEFAULT 0,
        reorder_point INTEGER NOT NULL DEFAULT 0,
        supplier_lead_days INTEGER NOT NULL DEFAULT 0,
        damaged_qty INTEGER NOT NULL DEFAULT 0,
        quarantined_qty INTEGER NOT NULL DEFAULT 0,
        returned_qty INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )''')


def downgrade():
    op.execute('DROP TABLE IF EXISTS inventory_controls')
    op.execute('DROP TABLE IF EXISTS audit_integrity')
    op.execute('DROP TABLE IF EXISTS support_interactions')
    op.execute('DROP TABLE IF EXISTS high_risk_action_policies')
    op.execute('DROP TABLE IF EXISTS admin_permission_grants')
