"""Indexes for bounded closed-candle scans and compact signal reads."""
from alembic import op

revision = 'p30_signal_performance'
down_revision = 'p25_current_signals'
branch_labels = None
depends_on = None

def upgrade():
    op.execute('CREATE INDEX ix_candles_closed_scan ON candles (instrument_id, resolution, open_time DESC) WHERE is_closed = true')
    op.create_index('ix_current_signals_action_computed','current_signals',['action','computed_at'],unique=False)

def downgrade():
    op.drop_index('ix_current_signals_action_computed',table_name='current_signals')
    op.drop_index('ix_candles_closed_scan',table_name='candles')
