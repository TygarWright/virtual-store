"""Complete business control layer: product costs + governance escalation fields.

Revision ID: a19d4e8c71f2
Revises: 8b12c7d4e9f0
"""
from alembic import op

revision = "a19d4e8c71f2"
down_revision = "8b12c7d4e9f0"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("ALTER TABLE products ADD COLUMN cost_price INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE products ADD COLUMN min_margin_percent INTEGER NOT NULL DEFAULT 15")
    op.execute("ALTER TABLE business_exceptions ADD COLUMN assigned_to INTEGER")
    op.execute("ALTER TABLE business_exceptions ADD COLUMN due_at TEXT")
    op.execute("ALTER TABLE business_exceptions ADD COLUMN escalated_at TEXT")
    op.execute("ALTER TABLE business_exceptions ADD COLUMN escalation_reason TEXT NOT NULL DEFAULT ''")

def downgrade():
    # SQLite cannot reliably drop columns across supported versions; leave these
    # additive columns in place during downgrade.
    pass
