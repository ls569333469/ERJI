"""PD2 capabilities, budgets, identities, outcomes and preferences."""
from alembic import op
import sqlalchemy as sa
revision="pd2_20260924"
down_revision="ec24309f1fb3"
branch_labels=None
depends_on=None

def upgrade():
    op.create_table("data_capabilities",
      sa.Column("key",sa.String(300),primary_key=True),sa.Column("provider",sa.String(40),nullable=False),
      sa.Column("capability",sa.String(60),nullable=False),sa.Column("subject",sa.String(180),nullable=False),
      sa.Column("status",sa.String(40),nullable=False),sa.Column("observed_at",sa.BigInteger),
      sa.Column("received_at",sa.BigInteger,nullable=False),sa.Column("expires_at",sa.BigInteger,nullable=False),
      sa.Column("next_refresh_at",sa.BigInteger),sa.Column("reason",sa.String(400)),
      sa.Column("raw_ref",sa.String(64)),sa.Column("payload",sa.JSON,nullable=False))
    for col in ("provider","capability","subject"):op.create_index("ix_data_capabilities_"+col,"data_capabilities",[col])
    op.create_table("provider_budgets",sa.Column("provider",sa.String(40),primary_key=True),sa.Column("month",sa.String(7),primary_key=True),
      sa.Column("used",sa.Integer,nullable=False),sa.Column("blocked_until",sa.BigInteger,nullable=False))
    op.create_table("identity_links",sa.Column("asset_id",sa.String(100),primary_key=True),sa.Column("provider",sa.String(40),primary_key=True),
      sa.Column("external_id",sa.String(120),nullable=False),sa.Column("status",sa.String(40),nullable=False),
      sa.Column("evidence",sa.JSON,nullable=False),sa.Column("known_at",sa.BigInteger,nullable=False))
    op.create_table("notification_targets",sa.Column("id",sa.String(100),primary_key=True),sa.Column("chat_id",sa.String(80),nullable=False),
      sa.Column("enabled",sa.Boolean,nullable=False),sa.Column("authorized_at",sa.BigInteger,nullable=False),sa.Column("subscriptions",sa.JSON,nullable=False))
    op.create_table("plan_outcomes",sa.Column("plan_id",sa.String(100),sa.ForeignKey("plans.id"),primary_key=True),
      sa.Column("horizon_hours",sa.Integer,primary_key=True),sa.Column("state",sa.String(40),nullable=False),
      sa.Column("evaluated_at",sa.BigInteger,nullable=False),sa.Column("payload",sa.JSON,nullable=False))
    op.create_table("user_preferences",sa.Column("key",sa.String(120),primary_key=True),sa.Column("payload",sa.JSON,nullable=False),sa.Column("updated_at",sa.BigInteger,nullable=False))

def downgrade():
    for name in ("user_preferences","plan_outcomes","notification_targets","identity_links","provider_budgets","data_capabilities"):
        op.drop_table(name)
