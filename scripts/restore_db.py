"""
Restore database SQLite RADsoftBOT dari file backup.

    python scripts/restore_db.py backups/radsoftbot_2026-11-15_2130.db

Urutan (dipilih supaya aman):
  1. Validasi file backup: integrity_check + tabel alembic_version ada.
  2. Cek bot tidak sedang berjalan (port web panel dipakai / DB terkunci) → batal.
  3. Minta konfirmasi: ketik RESTORE (lewati dengan --yes).
  4. Salin DB saat ini ke backups/pre-restore_radsoftbot_YYYY-MM-DD_HHMM.db
     (lewat backup API, termasuk isi -wal). Salinan ini TIDAK ikut dihapus
     oleh retensi backup_db.py.
  5. Salin backup ke file sementara di sebelah DB, cek integritas lagi.
  6. Hapus -wal / -shm DB lama (WAL lama tidak boleh "diterapkan" ke DB baru).
  7. Ganti file DB secara atomik (os.replace).
  8. Tampilkan versi skema; kalau lebih lama, jalankan `alembic upgrade head`.

Exit code: 0 sukses, 1 batal / argumen salah, 2 gagal.
"""
import argparse
import os
import socket
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import DATABASE_URL, WEB_HOST, WEB_PORT  # noqa: E402
from scripts.backup_db import (  # noqa: E402
    BACKUP_DIR, BACKUP_PREFIX, EXIT_CONFIG, EXIT_FAILED, EXIT_OK,
    ConfigError, integrity_check, now_wib, sqlite_copy, sqlite_path_from_url,
)

CONFIRM_WORD = "RESTORE"


class RestoreAborted(Exception):
    """Dibatalkan (exit code 1)."""


class RestoreError(Exception):
    """Gagal (exit code 2)."""


def schema_version(path: Path) -> str | None:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    except sqlite3.DatabaseError:
        return None
    finally:
        con.close()


def validate_backup(path: Path) -> str:
    if not path.is_file():
        raise RestoreAborted(f"File backup tidak ditemukan: {path}")
    result = integrity_check(path)
    if result != "ok":
        raise RestoreAborted(f"File backup tidak valid ({result}).")
    version = schema_version(path)
    if version is None:
        raise RestoreAborted("File ini bukan database RADsoftBOT (tabel alembic_version tidak ada).")
    return version


def bot_seems_running(db_path: Path, host: str = WEB_HOST, port: int = WEB_PORT) -> str | None:
    """Alasan kalau bot tampaknya berjalan, None kalau aman. Best effort."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return f"web panel masih menerima koneksi di {host}:{port}"
    except OSError:
        pass
    if db_path.is_file():
        con = sqlite3.connect(str(db_path), timeout=0)
        try:
            con.execute("BEGIN EXCLUSIVE")
            con.rollback()
        except sqlite3.OperationalError:
            return "database sedang dikunci proses lain"
        finally:
            con.close()
    return None


def restore(backup: Path, db_path: Path, backup_dir: Path = BACKUP_DIR,
            confirm=input, check_running=bot_seems_running, assume_yes: bool = False) -> Path | None:
    """Return path salinan pre-restore (None kalau DB lama belum ada)."""
    backup, db_path = backup.resolve(), db_path.resolve()
    if backup == db_path:
        raise RestoreAborted("File backup sama dengan database aktif.")

    version = validate_backup(backup)
    print(f"Backup valid: {backup.name} (skema {version})")

    reason = check_running(db_path)
    if reason:
        raise RestoreAborted(f"Bot tampaknya masih berjalan: {reason}. Hentikan bot dulu.")

    if not assume_yes:
        print(f"Database aktif {db_path} akan DITIMPA dengan {backup.name}.")
        answer = confirm(f"Ketik {CONFIRM_WORD} untuk melanjutkan: ")
        if answer.strip() != CONFIRM_WORD:
            raise RestoreAborted("Dibatalkan.")

    safety = None
    if db_path.is_file():
        backup_dir.mkdir(parents=True, exist_ok=True)
        safety = backup_dir / f"pre-restore_{BACKUP_PREFIX}_{now_wib():%Y-%m-%d_%H%M%S}.db"
        sqlite_copy(db_path, safety)
        print(f"Salinan DB saat ini: {safety}")

    tmp = db_path.with_name(f".{db_path.name}.restore-tmp")
    try:
        sqlite_copy(backup, tmp)
        if integrity_check(tmp) != "ok":
            raise RestoreError("Salinan sementara tidak valid.")
        for suffix in ("-wal", "-shm"):
            Path(f"{db_path}{suffix}").unlink(missing_ok=True)
        os.replace(tmp, db_path)
    except RestoreError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise RestoreError(f"Restore gagal: {type(e).__name__}: {e}. DB lama tetap ada"
                           + (f"; salinannya di {safety}" if safety else "") + ".") from e

    print(f"Restore selesai. Skema database: {schema_version(db_path)}.")
    print("Kalau skema backup lebih lama, jalankan: alembic upgrade head")
    return safety


def main(argv: list[str] | None = None, database_url: str | None = None,
         backup_dir: Path | None = None, confirm=input, check_running=bot_seems_running) -> int:
    ap = argparse.ArgumentParser(description="Restore database SQLite RADsoftBOT dari file backup.")
    ap.add_argument("backup", type=Path, help="file backup, mis. backups/radsoftbot_2026-11-15_2130.db")
    ap.add_argument("--yes", action="store_true", help="lewati konfirmasi (cek bot berjalan tetap dilakukan)")
    args = ap.parse_args(argv)

    try:
        db_path = sqlite_path_from_url(database_url or DATABASE_URL)
        restore(args.backup, db_path, backup_dir or BACKUP_DIR,
                confirm=confirm, check_running=check_running, assume_yes=args.yes)
        return EXIT_OK
    except (ConfigError, RestoreAborted) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_CONFIG
    except RestoreError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
