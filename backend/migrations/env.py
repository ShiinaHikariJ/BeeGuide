from alembic import context
from app.config import settings
from app.database import Base, engine
from app import models

context.config.set_main_option('sqlalchemy.url', settings.database_url.replace('%', '%%'))
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=engine.dialect.name == 'sqlite')
    with context.begin_transaction():
        context.run_migrations()
