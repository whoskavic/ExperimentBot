"""Langkah 9: backup & restore SQLite. Data fiktif."""
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
import pytz

import db.base as db_base
from config import BASE_DIR, TZ
from db.base import session_scope
from db.models import Source
from scripts import backup_db as B
from scripts import restore_db as R
from services.recap_store import EntryIn, SourceInfo, save_recap_day

NOW = TZ.localize(datetime(2026, 11, 15, 21, 30))
SRC = SourceInfo(800000000000000001, "channel", "opening-closing")


def db_file(engine) -> Path:
    return Path(engine.url.database)


def add_recap(day: int, name="ANDI"):
    save_recap_day(SRC, date(2026, 11, day), [EntryIn(900000000000000001, name, opening=True)],
                   "scheduler", date(2026, 11, 30))


def source_names(path: Path) -> list[str]:
    con = sqlite3.connect(str(path))
    try:
        return [r[0] for r in con.execute("SELECT display_name FROM recap_entries ORDER BY id")]
    finally:
        con.close()


def recap_days(path: Path) -> list[str]:
    con = sqlite3.connect(str(path))
    try:
        return [r[0] for r in con.execute("SELECT recap_date FROM recaps ORDER BY recap_date")]
    finally:
        con.close()


# ── URL / nama file ───────────────────────────────────────

def test_sqlite_path_resolved_like_db_base():
    assert B.sqlite_path_from_url("sqlite:///radsoftbot.db") == (BASE_DIR / "radsoftbot.db").resolve()


@pytest.mark.parametrize("url", ["postgresql://u:p@localhost/x", "sqlite:///:memory:"])
def test_non_sqlite_url_rejected(url):
    with pytest.raises(B.ConfigError):
        B.sqlite_path_from_url(url)


def test_main_non_sqlite_message(tmp_path, capsys):
    code = B.main([], database_url="postgresql://u:p@localhost/x", backup_dir=tmp_path)
    assert code == B.EXIT_CONFIG
    assert "hanya untuk SQLite" in capsys.readouterr().err


def test_backup_filename_uses_wib_and_avoids_collision(tmp_path):
    utc = pytz.utc.localize(datetime(2026, 11, 15, 14, 30, 59))          # = 21:30 WIB
    first = B.backup_filename(utc, tmp_path)
    assert first.name == "radsoftbot_2026-11-15_2130.db"
    first.touch()
    assert B.backup_filename(utc, tmp_path).name == "radsoftbot_2026-11-15_2130_2.db"
    # lewat tengah malam WIB → tanggal WIB, bukan tanggal UTC
    assert B.backup_filename(pytz.utc.localize(datetime(2026, 11, 15, 17, 5)), tmp_path).name \
        == "radsoftbot_2026-11-16_0005.db"


# ── backup ────────────────────────────────────────────────

def test_backup_while_other_connection_has_open_write(db, tmp_path):
    tmp_path = tmp_path / "backups"          # terpisah dari file DB test
    add_recap(12)
    add_recap(13)
    wal = Path(f"{db_file(db)}-wal")
    assert wal.exists() and wal.stat().st_size > 0        # data terbaru masih di WAL

    with session_scope(write=True) as s:                    # transaksi tulis terbuka (BEGIN IMMEDIATE)
        s.add(Source(discord_id=800000000000000099, kind="thread", name="belum commit"))
        s.flush()
        out = B.create_backup(db_file(db), tmp_path, NOW)

    assert out.name == "radsoftbot_2026-11-15_2130.db"
    assert B.integrity_check(out) == "ok"
    assert recap_days(out) == ["2026-11-12", "2026-11-13"]  # termasuk data yang masih di WAL
    con = sqlite3.connect(str(out))
    try:
        names = [r[0] for r in con.execute("SELECT name FROM sources")]
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"   # file tunggal
    finally:
        con.close()
    assert names == ["opening-closing"]                     # yang belum commit tidak ikut
    assert not Path(f"{out}-wal").exists()
    assert [p.name for p in tmp_path.iterdir()] == [out.name]   # tidak ada file .tmp tertinggal


def test_backup_integrity_failure_removes_file(db, tmp_path, monkeypatch, capsys):
    tmp_path = tmp_path / "backups"          # terpisah dari file DB test
    add_recap(12)
    monkeypatch.setattr(B, "integrity_check", lambda p: "*** in database main *** page 3 corrupt")
    with pytest.raises(B.BackupError, match="integrity_check gagal"):
        B.create_backup(db_file(db), tmp_path, NOW)
    assert list(tmp_path.iterdir()) == []

    code = B.main([], database_url=f"sqlite:///{db_file(db).as_posix()}", backup_dir=tmp_path)
    assert code == B.EXIT_FAILED and list(tmp_path.iterdir()) == []
    assert "integrity_check gagal" in capsys.readouterr().err


def test_backup_missing_db(tmp_path):
    code = B.main([], database_url=f"sqlite:///{(tmp_path / 'tidak-ada.db').as_posix()}",
                  backup_dir=tmp_path / "backups")
    assert code == B.EXIT_CONFIG and not (tmp_path / "backups").exists()


# ── retensi ───────────────────────────────────────────────

def make_files(d: Path) -> dict[str, Path]:
    d.mkdir(exist_ok=True)
    names = [
        "radsoftbot_2026-10-01_2359.db",       # tua → hapus
        "radsoftbot_2026-10-31_2130_2.db",     # tua (suffix) → hapus
        "radsoftbot_2026-11-01_2130.db",       # tepat 14 hari → simpan
        "radsoftbot_2026-11-15_0800.db",       # baru → simpan
        "radsoftbot_2026-13-01_0000.db",       # tanggal tidak valid → jangan sentuh
        "radsoftbot_2026-10-01.db",            # pola salah → jangan sentuh
        "radsoftbot_2026-10-01_2359.db.bak",   # pola salah
        "pre-restore_radsoftbot_2026-10-01_120000.db",   # salinan pre-restore → jangan sentuh
        "catatan.txt",
        ".radsoftbot_2026-10-01_2359.db.tmp",
    ]
    files = {}
    for n in names:
        (d / n).write_bytes(b"x")
        files[n] = d / n
    sub = d / "lama"
    sub.mkdir()
    (sub / "radsoftbot_2026-01-01_0000.db").write_bytes(b"x")   # di subfolder → jangan sentuh
    return files


def test_prune_only_old_matching_backups(tmp_path):
    bdir = tmp_path / "backups"
    files = make_files(bdir)
    outside = tmp_path / "radsoftbot_2026-01-01_0000.db"      # di luar folder backups
    outside.write_bytes(b"x")

    removed = B.prune_backups(bdir, 14, NOW)
    assert sorted(p.name for p in removed) == ["radsoftbot_2026-10-01_2359.db", "radsoftbot_2026-10-31_2130_2.db"]
    remaining = sorted(p.name for p in bdir.iterdir())
    assert remaining == sorted(set(files) - {p.name for p in removed} | {"lama"})
    assert (bdir / "lama" / "radsoftbot_2026-01-01_0000.db").exists()
    assert outside.exists()


def test_prune_keep_days_argument(tmp_path):
    bdir = tmp_path / "backups"
    make_files(bdir)
    removed = B.prune_backups(bdir, 30, NOW)
    assert [p.name for p in removed] == ["radsoftbot_2026-10-01_2359.db"]
    with pytest.raises(B.ConfigError):
        B.prune_backups(bdir, 0, NOW)


def test_prune_dry_run_deletes_nothing(tmp_path):
    bdir = tmp_path / "backups"
    make_files(bdir)
    before = sorted(p.name for p in bdir.iterdir())
    removed = B.prune_backups(bdir, 14, NOW, dry_run=True)
    assert len(removed) == 2
    assert sorted(p.name for p in bdir.iterdir()) == before


def test_prune_ignores_symlink(tmp_path):
    bdir = tmp_path / "backups"
    bdir.mkdir()
    target = tmp_path / "penting.db"
    target.write_bytes(b"x")
    try:
        os.symlink(target, bdir / "radsoftbot_2026-01-01_0000.db")
    except (OSError, NotImplementedError):
        pytest.skip("symlink tidak didukung di lingkungan ini")
    assert B.prune_backups(bdir, 14, NOW) == []
    assert target.exists()


def test_main_dry_run_changes_nothing(db, tmp_path, capsys):
    add_recap(12)
    bdir = tmp_path / "backups"
    make_files(bdir)
    before = sorted(p.name for p in bdir.iterdir())
    code = B.main(["--dry-run"], database_url=f"sqlite:///{db_file(db).as_posix()}", backup_dir=bdir)
    out = capsys.readouterr().out
    assert code == B.EXIT_OK
    assert sorted(p.name for p in bdir.iterdir()) == before       # tidak ada backup baru / file terhapus
    assert "[dry-run] Akan membuat backup" in out
    assert "[dry-run] Akan dihapus (> 14 hari): radsoftbot_2026-10-01_2359.db" in out


def test_main_backup_and_prune(db, tmp_path, capsys):
    add_recap(12)
    bdir = tmp_path / "backups"
    make_files(bdir)
    code = B.main(["--keep-days", "30"], database_url=f"sqlite:///{db_file(db).as_posix()}", backup_dir=bdir)
    out = capsys.readouterr().out
    assert code == B.EXIT_OK
    assert "Backup dibuat:" in out and "Dihapus (> 30 hari): radsoftbot_2026-10-01_2359.db" in out
    assert not (bdir / "radsoftbot_2026-10-01_2359.db").exists()
    new = [p for p, _ in B.find_backups(bdir) if p.stat().st_size > 1]
    assert len(new) == 1 and recap_days(new[0]) == ["2026-11-12"]


def test_main_invalid_keep_days(tmp_path, db):
    assert B.main(["--keep-days", "0"], database_url=f"sqlite:///{db_file(db).as_posix()}",
                  backup_dir=tmp_path) == B.EXIT_CONFIG


# ── restore ───────────────────────────────────────────────

@pytest.fixture
def restore_setup(db, tmp_path):
    """DB berisi data A → backup → DB diubah ke data B. Koneksi ditutup (= bot berhenti)."""
    add_recap(12, "ANDI")
    bdir = tmp_path / "backups"
    backup = B.create_backup(db_file(db), bdir, NOW)
    add_recap(13, "SITI")
    db_base.engine.dispose()
    return db_file(db), backup, bdir


def not_running(_db_path):
    return None


def test_restore_replaces_db_and_keeps_safety_copy(restore_setup):
    db_path, backup, bdir = restore_setup
    assert recap_days(db_path) == ["2026-11-12", "2026-11-13"]

    safety = R.restore(backup, db_path, bdir, confirm=lambda _: "RESTORE", check_running=not_running)

    assert recap_days(db_path) == ["2026-11-12"]                       # data A kembali
    assert B.integrity_check(db_path) == "ok"
    assert safety.name.startswith("pre-restore_radsoftbot_")
    assert recap_days(safety) == ["2026-11-12", "2026-11-13"]          # data B tersimpan
    assert not Path(f"{db_path}-wal").exists() and not Path(f"{db_path}-shm").exists()
    assert not list(db_path.parent.glob(".*restore-tmp"))
    assert all(p.name != safety.name for p, _ in B.find_backups(bdir))  # tidak ikut retensi

    # aplikasi bisa langsung memakai DB hasil restore
    add_recap(14, "BUDI")
    assert recap_days(db_path) == ["2026-11-12", "2026-11-14"]


def test_restore_removes_stale_wal_and_shm(restore_setup):
    db_path, backup, bdir = restore_setup
    Path(f"{db_path}-wal").write_bytes(b"wal lama")
    Path(f"{db_path}-shm").write_bytes(b"shm lama")
    R.restore(backup, db_path, bdir, assume_yes=True, check_running=not_running)
    assert not Path(f"{db_path}-wal").exists() and not Path(f"{db_path}-shm").exists()
    assert recap_days(db_path) == ["2026-11-12"]


@pytest.mark.parametrize("answer", ["", "restore", "ya", "RESTORE sekarang"])
def test_restore_requires_exact_confirmation(restore_setup, answer):
    db_path, backup, bdir = restore_setup
    with pytest.raises(R.RestoreAborted, match="Dibatalkan"):
        R.restore(backup, db_path, bdir, confirm=lambda _: answer, check_running=not_running)
    assert recap_days(db_path) == ["2026-11-12", "2026-11-13"]         # tidak berubah
    assert not list(bdir.glob("pre-restore_*"))


def test_restore_aborts_when_bot_running(restore_setup):
    db_path, backup, bdir = restore_setup
    with pytest.raises(R.RestoreAborted, match="masih berjalan"):
        R.restore(backup, db_path, bdir, assume_yes=True,
                  check_running=lambda p: "web panel masih menerima koneksi di 127.0.0.1:8080")
    assert recap_days(db_path) == ["2026-11-12", "2026-11-13"]


def test_bot_seems_running_detects_listening_port(restore_setup):
    import socket
    db_path, *_ = restore_setup
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    try:
        port = srv.getsockname()[1]
        assert "web panel" in R.bot_seems_running(db_path, "127.0.0.1", port)
    finally:
        srv.close()
    assert R.bot_seems_running(db_path, "127.0.0.1", port) is None


def test_bot_seems_running_detects_db_write_lock(restore_setup):
    db_path, *_ = restore_setup
    con = sqlite3.connect(str(db_path))
    con.execute("BEGIN IMMEDIATE")
    try:
        assert "dikunci" in R.bot_seems_running(db_path, "127.0.0.1", 1)
    finally:
        con.rollback()
        con.close()


@pytest.mark.parametrize("content", [b"bukan database", None])
def test_restore_rejects_invalid_backup(restore_setup, tmp_path, content):
    db_path, _, bdir = restore_setup
    bad = tmp_path / "rusak.db"
    if content is None:                         # SQLite valid tapi bukan DB RADsoftBOT
        sqlite3.connect(str(bad)).close()
        sqlite3.connect(str(bad)).execute("CREATE TABLE x (a)").connection.commit()
    else:
        bad.write_bytes(content)
    with pytest.raises(R.RestoreAborted):
        R.restore(bad, db_path, bdir, assume_yes=True, check_running=not_running)
    assert recap_days(db_path) == ["2026-11-12", "2026-11-13"]


def test_restore_main_exit_codes(restore_setup, capsys):
    db_path, backup, bdir = restore_setup
    url = f"sqlite:///{db_path.as_posix()}"
    assert R.main([str(backup)], database_url=url, backup_dir=bdir,
                  confirm=lambda _: "tidak", check_running=not_running) == R.EXIT_CONFIG
    assert R.main([str(backup), "--yes"], database_url=url, backup_dir=bdir,
                  check_running=not_running) == R.EXIT_OK
    assert recap_days(db_path) == ["2026-11-12"]
    assert "Restore selesai. Skema database: 0001." in capsys.readouterr().out


# ── dijalankan sebagai skrip ──────────────────────────────

def _run_script(name, *args, env_url):
    import subprocess
    import sys
    env = {**os.environ, "DATABASE_URL": env_url}
    return subprocess.run([sys.executable, str(BASE_DIR / "scripts" / name), *args],
                          capture_output=True, text=True, env=env, cwd=str(BASE_DIR), timeout=60)


@pytest.mark.parametrize("name, args", [("backup_db.py", ["--dry-run"]), ("restore_db.py", ["x.db", "--yes"])])
def test_scripts_postgres_url_clear_message_without_driver(name, args):
    """Tidak boleh crash karena driver PostgreSQL tidak terpasang; harus pesan yang jelas."""
    res = _run_script(name, *args, env_url="postgresql://u:p@localhost/x")
    assert res.returncode == 1, res.stderr
    assert "hanya untuk SQLite" in res.stderr
    assert "Traceback" not in res.stderr


def test_backup_script_dry_run_as_subprocess(db, tmp_path):
    add_recap(12)
    db_base.engine.dispose()
    res = _run_script("backup_db.py", "--dry-run", env_url=f"sqlite:///{db_file(db).as_posix()}")
    assert res.returncode == 0, res.stderr
    assert "[dry-run] Akan membuat backup dari test.db" in res.stdout


def test_restore_removes_leftover_wal_when_db_file_missing(restore_setup):
    """DB hilang tapi -wal/-shm sisa crash masih ada → harus dihapus sebelum DB baru dipasang
    (tidak ada koneksi SQLite yang sempat membersihkannya)."""
    db_path, backup, bdir = restore_setup
    db_path.unlink()
    Path(f"{db_path}-wal").write_bytes(b"wal lama")
    Path(f"{db_path}-shm").write_bytes(b"shm lama")

    safety = R.restore(backup, db_path, bdir, assume_yes=True, check_running=lambda p: None)

    assert safety is None                                    # tidak ada DB lama untuk disalin
    assert not Path(f"{db_path}-wal").exists() and not Path(f"{db_path}-shm").exists()
    assert recap_days(db_path) == ["2026-11-12"]
