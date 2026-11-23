"""Staged selection registry; no automatic activation or data migration."""
import sqlalchemy as sa
from alembic import op
revision='20261122_0032'
down_revision='20261120_0031'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('voice_selection_session_fence',
        sa.Column('installation_id',sa.Uuid(),sa.ForeignKey('installations.id'),primary_key=True),
        sa.Column('tenant_id',sa.Uuid(),sa.ForeignKey('tenants.id'),nullable=False),
        sa.Column('session_id',sa.Uuid(),nullable=False),
        sa.Column('credential_id',sa.Uuid(),sa.ForeignKey('connector_credentials.id'),nullable=True),
        sa.Column('active',sa.Boolean(),nullable=False))
    op.create_table('voice_selection_snapshot',
        sa.Column('installation_id',sa.Uuid(),sa.ForeignKey('installations.id'),primary_key=True),
        sa.Column('tenant_id',sa.Uuid(),sa.ForeignKey('tenants.id'),nullable=False),
        sa.Column('revision',sa.BigInteger(),nullable=False),sa.Column('registry_ids',sa.JSON(),nullable=False),
        sa.Column('session_id',sa.Uuid(),nullable=False),sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('voice_selection_member',
        sa.Column('installation_id',sa.Uuid(),sa.ForeignKey('installations.id'),primary_key=True),
        sa.Column('registry_id',sa.String(64),primary_key=True))

def downgrade():
    op.drop_table('voice_selection_member')
    op.drop_table('voice_selection_snapshot')
    op.drop_table('voice_selection_session_fence')
