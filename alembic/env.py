import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings
from app.core.db import Base
from app.models.organization import Organization  # noqa: F401
from app.models.cloud_account import CloudAccount  # noqa: F401
from app.models.finding import Finding  # noqa: F401
from app.models.policy_profile import PolicyProfile  # noqa: F401
from app.models.recommendation import Recommendation  # noqa: F401
from app.models.resource_snapshot import ResourceSnapshot  # noqa: F401
from app.models.tenant import Tenant  # noqa: F401
from app.models.notification import Notification  # noqa: F401
from app.models.approval_request import ApprovalAssignment, ApprovalRequest  # noqa: F401

# this is the Alembic Config object
config = context.config

# Ensure Alembic uses the runtime application DB URL.
config.set_main_option("sqlalchemy.url", settings.database_url)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Model's MetaData object for 'autogenerate' support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = settings.database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    configuration = config.get_section(config.config_ini_section)
    if configuration is None:
        configuration = {}

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
