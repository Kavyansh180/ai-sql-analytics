from logging.config import fileConfig
from alembic import context
from sqlalchemy import pool

from src.core.config import get_settings
from src.db.models.base import Base
# Import metadata models so Base.metadata is populated
from src.db.models.metadata import QueryAuditLog
from src.db.session import app_engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def include_object(object, name, type_, reflected, compare_to):
    """Only include objects in the app_metadata schema for Alembic migrations."""
    if type_ == "table":
        return object.schema == "app_metadata"
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    settings = get_settings()
    url = settings.get_app_database_url().render_as_string(hide_password=False)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema="app_metadata",
        include_schemas=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using injected connection or app_engine."""
    injected_connection = config.attributes.get("connection", None)
    if injected_connection is not None:
        context.configure(
            connection=injected_connection,
            target_metadata=target_metadata,
            version_table_schema="app_metadata",
            include_schemas=True,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()
        return

    with app_engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema="app_metadata",
            include_schemas=True,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

