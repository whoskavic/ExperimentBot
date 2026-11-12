from datetime import date

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

import db.base as db_base
from db.base import session_scope, resolve_url
from db.models import Base, Source, Recap, RecapEntry, RecapRevision
from config import BASE_DIR


def test_migration_matches_models(db):
    with db.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_sqlite_pragmas(db):
    with db.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000


def test_relative_sqlite_path_resolves_to_project_root():
    url = resolve_url("sqlite:///radsoftbot.db")
    assert url.database == str((BASE_DIR / "radsoftbot.db").resolve())
    assert resolve_url("sqlite:///:memory:").database == ":memory:"


def _make_recap(s, discord_id=1, d=date(2026, 11, 12)):
    src = Source(discord_id=discord_id, kind="channel", name="general")
    rec = Recap(source=src, recap_date=d, last_triggered_by="scheduler")
    s.add(rec)
    s.flush()
    return rec


def test_unique_source_and_date(db):
    with session_scope(write=True) as s:
        rec = _make_recap(s)
        src_id = rec.source_id

    with pytest.raises(IntegrityError):
        with session_scope(write=True) as s:
            s.add(Recap(source_id=src_id, recap_date=date(2026, 11, 12), last_triggered_by="x"))


def test_unique_user_per_recap(db):
    with pytest.raises(IntegrityError):
        with session_scope(write=True) as s:
            rec = _make_recap(s)
            s.add_all([
                RecapEntry(recap=rec, discord_user_id=42, display_name="A"),
                RecapEntry(recap=rec, discord_user_id=42, display_name="A"),
            ])


def test_foreign_key_enforced(db):
    with pytest.raises(IntegrityError):
        with session_scope(write=True) as s:
            s.add(Recap(source_id=999, recap_date=date(2026, 11, 12), last_triggered_by="x"))


def test_delete_recap_cascades_in_database(db):
    with session_scope(write=True) as s:
        rec = _make_recap(s)
        s.add(RecapEntry(recap=rec, discord_user_id=1, display_name="A", opening=True))
        s.add(RecapRevision(recap=rec, revision_no=1, snapshot={"entries": []}, triggered_by="x"))
        rec_id = rec.id

    # hapus lewat SQL langsung → yang menghapus anak adalah ON DELETE CASCADE di DB
    with db.begin() as conn:
        conn.exec_driver_sql(f"DELETE FROM recaps WHERE id = {rec_id}")

    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(RecapEntry)) == 0
        assert s.scalar(select(func.count()).select_from(RecapRevision)) == 0


def test_json_snapshot_and_defaults_roundtrip(db):
    with session_scope(write=True) as s:
        rec = _make_recap(s)
        s.add(RecapRevision(recap=rec, revision_no=1,
                            snapshot={"entries": [{"discord_user_id": 1, "opening": True}]},
                            triggered_by="panel:admin"))

    with session_scope() as s:
        rec = s.scalars(select(Recap)).one()
        assert rec.revision_count == 0
        assert rec.created_at is not None and rec.source.team is None
        assert rec.revisions[0].snapshot["entries"][0]["opening"] is True


def test_write_session_uses_begin_immediate(db):
    """Transaksi tulis memegang write lock sejak BEGIN (belum ada statement apa pun)."""
    import sqlite3
    with session_scope(write=True) as s:
        s.connection()  # memicu BEGIN IMMEDIATE
        other = sqlite3.connect(db.url.database, timeout=0)
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            other.execute("BEGIN IMMEDIATE")
        other.close()
