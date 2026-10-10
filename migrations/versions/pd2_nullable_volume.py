"""Preserve unavailable base-unit volume in USD-only pool OHLCV."""
from alembic import op
import sqlalchemy as sa
revision='pd2_nullable_volume'
down_revision='pd2_20260924'
branch_labels=None
depends_on=None
def upgrade():
    op.alter_column('candles','base_volume',existing_type=sa.Numeric(38,18),nullable=True)
def downgrade():
    connection=op.get_bind()
    if connection.execute(sa.text('SELECT COUNT(*) FROM candles WHERE base_volume IS NULL')).scalar():
        raise RuntimeError('Cannot discard unknown-volume observations; restore a compatible backup')
    op.alter_column('candles','base_volume',existing_type=sa.Numeric(38,18),nullable=False)
