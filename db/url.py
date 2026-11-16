"""
Resolusi DATABASE_URL tanpa membuat engine.

Dipisah dari db/base.py supaya skrip (backup/restore) bisa membaca URL tanpa
memuat driver database (mis. psycopg untuk PostgreSQL).
"""
from pathlib import Path

from sqlalchemy.engine import URL, make_url

from config import BASE_DIR


def resolve_url(raw: str) -> URL:
    """Path SQLite relatif → absolut terhadap folder proyek (bukan CWD)."""
    url = make_url(raw)
    if url.get_backend_name() == "sqlite":
        db = url.database
        if db and db != ":memory:" and not db.startswith("file:"):
            p = Path(db)
            if not p.is_absolute():
                url = url.set(database=str((BASE_DIR / p).resolve()))
    return url
