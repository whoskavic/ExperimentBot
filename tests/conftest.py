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


# ── web / auth ────────────────────────────────────────────

ADMIN_USER = "admin"
ADMIN_PASS = "rahasia-panjang-123"     # fiktif, hanya untuk test


@pytest.fixture
def app(db):
    from web.server import app as flask_app
    from web.auth import limiter
    flask_app.config.update(SECRET_KEY="test-secret-key", TESTING=True)
    limiter.reset()
    yield flask_app
    limiter.reset()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin(db):
    from web.auth import create_admin
    create_admin(ADMIN_USER, ADMIN_PASS)
    return ADMIN_USER, ADMIN_PASS


def get_csrf(client) -> str:
    return client.get("/api/csrf-token").get_json()["csrf_token"]


def do_login(client, username=ADMIN_USER, password=ADMIN_PASS, next_=None, with_csrf=True):
    data = {"username": username, "password": password}
    if with_csrf:
        data["csrf_token"] = get_csrf(client)
    url = "/login" + (f"?next={next_}" if next_ else "")
    return client.post(url, data=data)


@pytest.fixture
def auth_client(client, admin):
    """Client yang sudah login. `auth_client.csrf` = token untuk header X-CSRFToken."""
    res = do_login(client)
    assert res.status_code == 302, res.get_data(as_text=True)
    client.csrf = client.get("/api/me").get_json()["csrf_token"]
    return client
