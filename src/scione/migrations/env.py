"""Run against the caller-owned connection without URL logging."""

from alembic import context

connection = context.config.attributes["connection"]
context.configure(connection=connection)
with context.begin_transaction():
    context.run_migrations()
