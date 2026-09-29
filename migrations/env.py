from alembic import context
from engine.db import Base, engine as db_engine
import engine.models  # registers frozen migration baseline candidates
target_metadata = Base.metadata
if context.is_offline_mode():
    context.configure(url=str(db_engine.url), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction(): context.run_migrations()
else:
    with db_engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction(): context.run_migrations()
