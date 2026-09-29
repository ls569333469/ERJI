"""Worker-generated compact signal read model; no historical data mutation."""
from alembic import op
import sqlalchemy as sa
revision = 'p25_current_signals'
down_revision = 'pd2_snapshot_datasets'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('current_signals',
        sa.Column('id', sa.String(180), primary_key=True),
        sa.Column('asset_id', sa.String(100), nullable=False),
        sa.Column('instrument_id', sa.String(180), sa.ForeignKey('instruments.id'), nullable=False),
        sa.Column('action', sa.String(30), nullable=False),
        sa.Column('first_seen_at', sa.BigInteger(), nullable=False),
        sa.Column('computed_at', sa.BigInteger(), nullable=False),
        sa.Column('valid_until', sa.BigInteger(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False))
    for col in ('asset_id', 'action', 'first_seen_at', 'valid_until'):
        op.create_index('ix_current_signals_' + col, 'current_signals', [col])

def downgrade():
    op.drop_table('current_signals')
