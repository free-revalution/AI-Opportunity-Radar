"""Alembic environment (sync).

Alembic's autogenerate pipeline requires a sync DBAPI. We use the sync URL
from `Settings.database_url_sync` (`postgresql://...`) but the runtime
app uses `Settings.database_url` (`postgresql+asyncpg://...`).

The env variable `ALEMBIC_DATABASE_URL` overrides the URL — useful when
running autogenerate against a local sqlite for tests.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override URL: env var first, then app setting.
alembic_url = os.environ.get("ALEMBIC_DATABASE_URL") or get_settings().database_url_sync
config.set_main_option("sqlalchemy.url", alembic_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL statements to stdout without a live DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live database connection."""
    cfg_section = config.get_section(config.config_ini_section) or {}
    connectable = engine_from_config(
        cfg_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()