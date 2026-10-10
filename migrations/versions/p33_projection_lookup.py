"""Index persisted provider evidence without blocking ongoing collection writes."""
from alembic import op

revision = 'p33_projection_lookup'
down_revision = 'p30_signal_performance'
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.execute('CREATE INDEX CONCURRENTLY ix_raw_source_endpoint_time ON raw_events (source, endpoint, received_at DESC)')


def downgrade():
    with op.get_context().autocommit_block():
        op.execute('DROP INDEX CONCURRENTLY ix_raw_source_endpoint_time')
