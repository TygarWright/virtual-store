"""Add inventory reservation correlation to orders.

Revision ID: 0f4c2d9ab7e1
Revises: 572f1204729e
"""
from alembic import op
import sqlalchemy as sa

revision = "0f4c2d9ab7e1"
down_revision = "572f1204729e"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.add_column(sa.Column("inventory_reservation_id", sa.String(), nullable=True))
        batch_op.create_index("idx_orders_inventory_reservation", ["inventory_reservation_id"], unique=False)


def downgrade():
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.drop_index("idx_orders_inventory_reservation")
        batch_op.drop_column("inventory_reservation_id")
