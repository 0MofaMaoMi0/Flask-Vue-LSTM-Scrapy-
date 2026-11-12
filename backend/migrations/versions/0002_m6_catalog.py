"""Authorized M6 test migration, compiled from the unchanged frozen authority."""
import os
from alembic import op
from sqlalchemy import text
from app.m6_database import environment_config, verify_m6_connection
from catalog.m6_contract import authority

revision = '0002_catalog'
down_revision = '0001_auth'
branch_labels = None
depends_on = None


def upgrade():
    config = environment_config(os.environ)
    if config['M6_ISOLATED'] is not True:
        raise RuntimeError('Explicit M6 migration environment required')
    connection = op.get_bind()
    verify_m6_connection(connection, config, migration=True)
    bundle = authority()
    # MySQL commits DDL individually. Persist the next operation before running
    # it; an interruption leaves a durable audit trail, never an automatic retry.
    from pathlib import Path
    import json
    path = Path(os.environ.get('MI2_M6_DDL_LEDGER', ''))
    if not path.is_absolute() or path.exists():
        raise RuntimeError('New absolute DDL ledger required; audit existing runs')
    with path.open('x', encoding='utf-8') as handle:
        os.chmod(path, 0o600)
        handle.flush()
        os.fsync(handle.fileno())
    def record(statement, status):
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'object': statement['object'], 'sha256': statement['sha256'], 'status': status}) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
    for statement in bundle['statements']:
        record(statement, 'pending')
        connection.execute(text(statement['sql']))
        record(statement, 'applied')


def downgrade():
    raise RuntimeError('Destructive downgrade requires separate approval')
