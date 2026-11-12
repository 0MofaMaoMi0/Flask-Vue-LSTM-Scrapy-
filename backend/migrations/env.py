from alembic import context
from app import create_app
from app.models import Base
import os
from sqlalchemy import create_engine
from app.m6_database import environment_config, validate_m6_url, verify_m6_connection
config = environment_config(os.environ)
if config['M6_ISOLATED']:
    url = validate_m6_url(os.environ.get('MI2_MIGRATION_DATABASE_URL', ''), config, migration=True)
    engine = create_engine(url, hide_parameters=True, pool_pre_ping=True)
else:
    app=create_app({'TESTING':os.environ.get('MI2_MODE')=='test'})
    engine=app.extensions['auth_engine']
with engine.connect() as connection:
    if config['M6_ISOLATED']:
        verify_m6_connection(connection, config, migration=True)
        connection.commit()
    context.configure(connection=connection,target_metadata=Base.metadata)
    with context.begin_transaction():context.run_migrations()
