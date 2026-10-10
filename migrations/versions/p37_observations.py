"""Independent anomaly observations; retain all existing plans."""
from alembic import op
import sqlalchemy as sa
revision='p37_observations'
down_revision='p34_capability_provenance'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('observations',sa.Column('id',sa.String(64),primary_key=True),sa.Column('instrument_id',sa.String(180),nullable=False),sa.Column('asset_id',sa.String(100),nullable=False),sa.Column('direction',sa.String(10),nullable=False),sa.Column('first_at',sa.BigInteger(),nullable=False),sa.Column('last_at',sa.BigInteger(),nullable=False),sa.Column('visible_at',sa.BigInteger(),nullable=True),sa.Column('status',sa.String(30),nullable=False),sa.Column('version',sa.BigInteger(),nullable=False),sa.Column('payload',sa.JSON(),nullable=False))
    for name in ('instrument_id','asset_id','last_at','status'):op.create_index('ix_observations_'+name,'observations',[name])

def downgrade():
    op.drop_table('observations')
