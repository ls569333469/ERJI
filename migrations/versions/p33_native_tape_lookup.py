"""Bound native evidence lookup to one instrument before reading time windows."""
from alembic import op

revision='p33_native_tape_lookup'
down_revision='p33_projection_lookup'
branch_labels=None
depends_on=None


def upgrade():
    with op.get_context().autocommit_block():
        op.execute("CREATE INDEX CONCURRENTLY ix_raw_native_instrument_time ON raw_events (source, (CAST(payload ->> 'instrument_id' AS VARCHAR)), received_at) WHERE endpoint IN ('ws:trade','ws:liquidation')")


def downgrade():
    with op.get_context().autocommit_block():
        op.execute('DROP INDEX CONCURRENTLY ix_raw_native_instrument_time')
