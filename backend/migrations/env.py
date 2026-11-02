from alembic import context
from app import create_app
from app.models import Base
app=create_app({'TESTING':__import__('os').environ.get('MI2_MODE')=='test'})
with app.extensions['auth_engine'].connect() as connection:
    context.configure(connection=connection,target_metadata=Base.metadata)
    with context.begin_transaction():context.run_migrations()
