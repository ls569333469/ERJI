"""Bounded observation reads and durable task admission; no historical deletion."""
from alembic import op
revision='p38_query_indexes'
down_revision='p37_observations'
branch_labels=None
depends_on=None

def upgrade():
    op.create_index('ix_observations_asset_status_last','observations',['asset_id','status','last_at'])
    op.create_index('ix_tasks_kind_due','tasks',['kind','state','available_at'])

def downgrade():
    op.drop_index('ix_tasks_kind_due',table_name='tasks')
    op.drop_index('ix_observations_asset_status_last',table_name='observations')
