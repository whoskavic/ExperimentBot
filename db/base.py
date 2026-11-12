"""
Engine & session.

- Satu engine per proses, session per operasi (`session_scope`).
- SQLite: WAL, foreign_keys ON, busy_timeout, dan transaksi tulis memakai
  `BEGIN IMMEDIATE` supaya dua rekap bersamaan saling menunggu (bukan gagal
  SQLITE_BUSY saat upgrade lock read → write).
- Semua kode SQLite-spesifik hanya aktif untuk dialect sqlite, jadi pindah ke
  PostgreSQL cukup dengan mengganti DATABASE_URL.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url, URL
from sqlalchemy.orm import Session, sessionmaker

from config import BASE_DIR, DATABASE_URL

_WRITE_OPT = "radsoft_begin_immediate"


def utcnow() -> datetime:
    """Timestamp disimpan sebagai UTC naive (SQLite tidak punya tipe ber-zona)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def resolve_url(raw: str) -> URL:
    """Path SQLite relatif → absolut terhadap folder proyek (bukan CWD)."""
    url = make_url(raw)
    if url.get_backend_name() == "sqlite":
        db = url.database
        if db and db != ":memory:" and not db.startswith("file:"):
            from pathlib import Path
            p = Path(db)
            if not p.is_absolute():
                url = url.set(database=str((BASE_DIR / p).resolve()))
    return url


def _install_sqlite_hooks(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):
        # Matikan transaksi implisit pysqlite; BEGIN dikontrol di event "begin".
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn):
        if conn.get_execution_options().get(_WRITE_OPT):
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            conn.exec_driver_sql("BEGIN")


def make_engine(raw_url: str) -> Engine:
    url = resolve_url(raw_url)
    kwargs = {}
    if url.get_backend_name() == "sqlite":
        # Dipakai dari thread Flask dan worker asyncio.to_thread.
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    if url.get_backend_name() == "sqlite":
        _install_sqlite_hooks(engine)
    return engine


engine: Engine = make_engine(DATABASE_URL)
_read_session  = sessionmaker(bind=engine, expire_on_commit=False)
_write_session = sessionmaker(bind=engine.execution_options(**{_WRITE_OPT: True}), expire_on_commit=False)


def configure(raw_url: str) -> Engine:
    """Ganti engine (dipakai test). Engine lama di-dispose."""
    global engine
    engine.dispose()
    engine = make_engine(raw_url)
    _read_session.configure(bind=engine)
    _write_session.configure(bind=engine.execution_options(**{_WRITE_OPT: True}))
    return engine


@contextmanager
def session_scope(write: bool = False) -> Iterator[Session]:
    """
    Satu transaksi singkat. Commit kalau sukses, rollback kalau error.
    `write=True` → BEGIN IMMEDIATE di SQLite.
    """
    session = (_write_session if write else _read_session)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
