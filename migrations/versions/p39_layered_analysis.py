"""Additive T1/T2/T3 demand and causal-result schema; no history rewrite."""
from alembic import op
import sqlalchemy as sa

revision = 'p39_layered_analysis'
down_revision = 'p39_snapshot_latest'
branch_labels = None
depends_on = None

def upgrade():
    # Frozen DDL: future ORM changes must not alter an already released migration.
    op.create_table('analysis_demands',
        sa.Column('id',sa.String(64),primary_key=True),
        sa.Column('instrument_id',sa.String(180),sa.ForeignKey('instruments.id'),nullable=False),
        sa.Column('origin_type',sa.String(40),nullable=False),sa.Column('source_id',sa.String(180),nullable=False),
        sa.Column('state',sa.String(30),nullable=False),sa.Column('created_at',sa.BigInteger(),nullable=False),
        sa.Column('updated_at',sa.BigInteger(),nullable=False),sa.Column('expires_at',sa.BigInteger()),
        sa.Column('payload',sa.JSON(),nullable=False))
    op.create_index('ix_analysis_demands_instrument_id','analysis_demands',['instrument_id'])
    op.create_index('ix_analysis_demands_live','analysis_demands',['state','expires_at','instrument_id'])
    op.create_table('analysis_results',
        sa.Column('id',sa.String(64),primary_key=True),
        sa.Column('instrument_id',sa.String(180),sa.ForeignKey('instruments.id'),nullable=False),
        sa.Column('work_kind',sa.String(30),nullable=False),sa.Column('input_version',sa.String(64),nullable=False),
        sa.Column('computed_at',sa.BigInteger(),nullable=False),sa.Column('payload',sa.JSON(),nullable=False))
    op.create_index('ix_analysis_results_instrument_id','analysis_results',['instrument_id'])
    op.create_index('ix_analysis_results_recent','analysis_results',['computed_at','work_kind'])
    op.create_table('market_analysis_states',
        sa.Column('instrument_id',sa.String(180),sa.ForeignKey('instruments.id'),primary_key=True),
        sa.Column('work_kind',sa.String(30),primary_key=True),sa.Column('input_version',sa.String(64)),
        sa.Column('result_id',sa.String(64),sa.ForeignKey('analysis_results.id')),
        sa.Column('analysis_status',sa.String(30),nullable=False),sa.Column('strategy_result',sa.String(40),nullable=False),
        sa.Column('last_attempt_at',sa.BigInteger(),nullable=False),sa.Column('last_success_at',sa.BigInteger()),
        sa.Column('next_check_at',sa.BigInteger(),nullable=False),sa.Column('payload',sa.JSON(),nullable=False))

def downgrade():
    for name in ('market_analysis_states','analysis_results','analysis_demands'):op.drop_table(name)
