"""Latest plan reads without lifetime JSON scans; no history rewrite."""
from alembic import op
revision='p39_snapshot_latest'
down_revision='p38_query_indexes'
branch_labels=None
depends_on=None

def upgrade():
    with op.get_context().autocommit_block():
        op.execute('CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_signal_snapshots_plan_latest ON signal_snapshots (plan_id, as_of DESC)')

def downgrade():
    with op.get_context().autocommit_block():
        op.execute('DROP INDEX CONCURRENTLY IF EXISTS ix_signal_snapshots_plan_latest')
