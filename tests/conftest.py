from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

import db.base as db_base
from config import BASE_DIR, DATABASE_URL


def _alembic_cfg(url: str) -> Config:
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture
def db(db_url):
    """DB file sementara, skema dibuat lewat migrasi Alembic (bukan create_all)."""
    command.upgrade(_alembic_cfg(db_url), "head")
    engine = db_base.configure(db_url)
    yield engine
    db_base.configure(DATABASE_URL)  # kembalikan engine default
