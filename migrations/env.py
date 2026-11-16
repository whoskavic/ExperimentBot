from logging.config import fileConfig

from alembic import context

import db.base as db_base
from db.models import Base

config = context.config

if config.config_file_name is not None:
    # Jangan matikan logger aplikasi yang sudah ada (mis. saat migrasi
    # dijalankan dari dalam proses / test).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    # Prioritas: -x url=... / config programatik (test) → DATABASE_URL (.env)
    url = context.get_x_argument(as_dictionary=True).get("url") or config.get_main_option("sqlalchemy.url")
    return url or db_base.DATABASE_URL


def run_migrations_offline() -> None:
    context.configure(
        url=db_base.resolve_url(_url()).render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Pakai make_engine supaya pragma (WAL, foreign_keys) sama dengan aplikasi.
    engine = db_base.make_engine(_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,   # ALTER TABLE di SQLite lewat batch mode
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
