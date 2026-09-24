"""Content-addressed immutable chart datasets."""
from alembic import op
import sqlalchemy as sa
revision='pd2_snapshot_datasets'
down_revision='pd2_nullable_volume'
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('snapshot_datasets',sa.Column('id',sa.String(64),primary_key=True),sa.Column('created_at',sa.BigInteger,nullable=False),sa.Column('payload',sa.JSON,nullable=False))
def downgrade():
    op.drop_table('snapshot_datasets')
