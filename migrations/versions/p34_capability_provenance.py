"""Keep successful capability provenance separate from failed attempts."""
from alembic import op
import sqlalchemy as sa

revision = 'p34_capability_provenance'
down_revision = 'p33_native_tape_lookup'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('data_capabilities', sa.Column('last_success_at', sa.BigInteger(), nullable=True))
    op.add_column('data_capabilities', sa.Column('last_success_raw_ref', sa.String(64), nullable=True))
    op.add_column('data_capabilities', sa.Column('last_attempt_at', sa.BigInteger(), nullable=True))
    op.add_column('data_capabilities', sa.Column('last_attempt_raw_ref', sa.String(64), nullable=True))
    # Only provable successful legacy values may acquire success lineage.
    # Failed legacy rows can already point at the wrong raw response.
    op.execute("UPDATE data_capabilities d SET last_success_at=d.received_at, last_success_raw_ref=d.raw_ref FROM raw_events r WHERE d.raw_ref=r.id AND d.status IN ('fresh','source_time_unknown') AND d.payload::jsonb=r.payload::jsonb AND d.received_at=r.received_at")
    op.execute("UPDATE data_capabilities d SET raw_ref=NULL, reason=LEFT(COALESCE(reason, '') || '; legacy value provenance unverified',400) WHERE d.raw_ref IS NOT NULL AND NOT EXISTS (SELECT 1 FROM raw_events r WHERE r.id=d.raw_ref AND r.payload::jsonb=d.payload::jsonb)")

def downgrade():
    op.drop_column('data_capabilities', 'last_attempt_raw_ref')
    op.drop_column('data_capabilities', 'last_attempt_at')
    op.drop_column('data_capabilities', 'last_success_raw_ref')
    op.drop_column('data_capabilities', 'last_success_at')
