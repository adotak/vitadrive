from __future__ import annotations

import os

from alembic import context
from sqlalchemy import Connection, create_engine, event, pool

from vitadrive.storage import database_url, metadata

config = context.config
target_metadata = metadata


def _database_target() -> str:
    arguments = context.get_x_argument(as_dictionary=True)
    return (arguments.get("db") or os.environ.get("DATABASE_URL")
            or os.environ.get("VITADRIVE_DB") or "vitadrive.db")


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(_database_target()),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations_online(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run_migrations_online(connection)
        return

    url = database_url(_database_target())
    connect_args = {"prepare_threshold": None} if url.startswith("postgresql") else {}
    engine = create_engine(url, poolclass=pool.NullPool, connect_args=connect_args)
    if url.startswith("sqlite"):
        event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys = ON"))
    try:
        with engine.connect() as connection:
            _run_migrations_online(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
