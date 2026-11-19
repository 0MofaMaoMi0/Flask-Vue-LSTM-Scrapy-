"""Independent support schema for import receipts and authenticated behavior."""
import json
import os
from pathlib import Path
from alembic import op
from app.m6_database import environment_config, verify_m6_connection
from app.m6_activity import activity_tables

revision = '0003_m6_activity'
down_revision = '0002_catalog'
branch_labels = None
depends_on = None


def upgrade():
    config = environment_config(os.environ)
    if config['M6_ISOLATED'] is not True:
        raise RuntimeError('M6-only support migration')
    connection = op.get_bind()
    verify_m6_connection(connection, config, migration=True)
    tables = activity_tables(os.environ.get('MI2_M6_ACTIVITY_SCHEMA'), config['M6_SCHEMA'])
    parent = Path(os.environ.get('MI2_M6_DDL_LEDGER', ''))
    if not parent.is_absolute():
        raise RuntimeError('Explicit M6 DDL ledger required')
    ledger = parent.with_name(revision+'.jsonl')
    with ledger.open('x', encoding='utf-8') as handle:
        os.chmod(ledger, 0o600)
        for table in tables.values():
            handle.write(json.dumps({'table':table.name,'status':'pending'})+'\n');handle.flush();os.fsync(handle.fileno())
            table.create(connection, checkfirst=False)
            handle.write(json.dumps({'table':table.name,'status':'applied'})+'\n');handle.flush();os.fsync(handle.fileno())


def downgrade():
    raise RuntimeError('Destructive support downgrade requires separate approval')
