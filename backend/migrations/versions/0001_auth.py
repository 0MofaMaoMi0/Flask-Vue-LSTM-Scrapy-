"""Initial isolated authentication tables."""
from alembic import op
from app.models import Base
revision='0001_auth'
down_revision=None
branch_labels=None
depends_on=None
def upgrade(): Base.metadata.create_all(op.get_bind(),checkfirst=False)
def downgrade(): raise RuntimeError('Destructive downgrade requires separate approval')
