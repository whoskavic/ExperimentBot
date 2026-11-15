"""
Backup database SQLite RADsoftBOT.

    python scripts/backup_db.py                 # backup + hapus backup > 14 hari
    python scripts/backup_db.py --keep-days 30
    python scripts/backup_db.py --dry-run       # hanya tampilkan rencana, tidak mengubah apa pun

- Memakai backup API sqlite3 (`Connection.backup`): aman walaupun bot sedang
  berjalan, termasuk data yang masih di file -wal.
- Hasil: backups/radsoftbot_YYYY-MM-DD_HHMM.db (waktu WIB), file tunggal
  (journal_mode=DELETE, tanpa -wal/-shm).
- Setelah backup: PRAGMA integrity_check. Gagal → file dihapus, exit code 2.
- Retensi hanya menyentuh file di dalam folder backups/ yang namanya cocok
  BACKUP_RE. File lain (termasuk salinan pre-restore) tidak pernah dihapus.

Exit code: 0 sukses, 1 konfigurasi/argumen salah, 2 backup gagal / tidak valid.
"""
import argparse
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytz  # noqa: E402

from config import BASE_DIR, DATABASE_URL, TZ  # noqa: E402
from db.url import resolve_url  # noqa: E402  (tanpa membuat engine / memuat driver)

BACKUP_DIR       = BASE_DIR / "backups"
BACKUP_PREFIX    = "radsoftbot"
DEFAULT_KEEP     = 14
BACKUP_RE        = re.compile(r"^radsoftbot_(\d{4}-\d{2}-\d{2})_(\d{4})(?:_(\d+))?\.db$")

EXIT_OK, EXIT_CONFIG, EXIT_FAILED = 0, 1, 2


class BackupError(Exception):
    """Backup gagal / file tidak valid (exit code 2)."""


class ConfigError(Exception):
    """DATABASE_URL / argumen tidak cocok (exit code 1)."""


# ── helpers (dipakai juga oleh restore_db.py) ─────────────

def now_wib() -> datetime:
    return datetime.now(pytz.utc).astimezone(TZ)


def sqlite_path_from_url(raw_url: str) -> Path:
    """DATABASE_URL → path file SQLite absolut (resolusi sama dengan db/base.py)."""
    url = resolve_url(raw_url)
    if url.get_backend_name() != "sqlite":
        raise ConfigError(
            f"Skrip ini hanya untuk SQLite. DATABASE_URL memakai '{url.get_backend_name()}' — "
            "pakai tool backup bawaan database tersebut (mis. pg_dump untuk PostgreSQL)."
        )
    if not url.database or url.database == ":memory:" or url.database.startswith("file:"):
        raise ConfigError("DATABASE_URL harus menunjuk ke file SQLite (bukan :memory: / URI).")
    return Path(url.database)


def integrity_check(path: Path) -> str:
    """'ok' kalau file valid, selain itu pesan dari SQLite."""
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            rows = con.execute("PRAGMA integrity_check").fetchall()
        finally:
            con.close()
    except sqlite3.DatabaseError as e:
        return f"{type(e).__name__}: {e}"
    return "ok" if rows == [("ok",)] else "; ".join(str(r[0]) for r in rows[:5])


def sqlite_copy(src: Path, dst: Path) -> None:
    """Salin DB lewat backup API (konsisten walau sedang dipakai) → file tunggal."""
    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(dst))
    try:
        s.backup(d)
        d.execute("PRAGMA journal_mode=DELETE")    # tanpa -wal/-shm, satu file saja
        d.commit()
    finally:
        d.close()
        s.close()


def backup_filename(ts: datetime, backup_dir: Path) -> Path:
    base = f"{BACKUP_PREFIX}_{ts.astimezone(TZ):%Y-%m-%d_%H%M}"
    candidate = backup_dir / f"{base}.db"
    n = 2
    while candidate.exists():                      # dua backup di menit yang sama
        candidate = backup_dir / f"{base}_{n}.db"
        n += 1
    return candidate


def parse_backup_time(name: str) -> datetime | None:
    m = BACKUP_RE.match(name)
    if not m:
        return None
    try:
        return TZ.localize(datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H%M"))
    except ValueError:
        return None


# ── backup ────────────────────────────────────────────────

def create_backup(db_path: Path, backup_dir: Path = BACKUP_DIR, now: datetime | None = None) -> Path:
    if not db_path.is_file():
        raise ConfigError(f"File database tidak ditemukan: {db_path}")

    backup_dir.mkdir(parents=True, exist_ok=True)
    final = backup_filename(now or now_wib(), backup_dir)
    tmp = final.with_name(f".{final.name}.tmp")     # tidak cocok BACKUP_RE

    try:
        sqlite_copy(db_path, tmp)
        result = integrity_check(tmp)
        if result != "ok":
            raise BackupError(f"integrity_check gagal: {result}")
        tmp.replace(final)
    except BackupError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise BackupError(f"Backup gagal: {type(e).__name__}: {e}") from e
    return final


# ── retensi ───────────────────────────────────────────────

def find_backups(backup_dir: Path) -> list[tuple[Path, datetime]]:
    """Hanya file biasa langsung di backup_dir yang namanya cocok BACKUP_RE."""
    if not backup_dir.is_dir():
        return []
    root = backup_dir.resolve()
    found = []
    for p in backup_dir.iterdir():
        if p.is_symlink() or not p.is_file() or p.resolve().parent != root:
            continue
        ts = parse_backup_time(p.name)
        if ts is not None:
            found.append((p, ts))
    return sorted(found, key=lambda x: (x[1], x[0].name))


def prune_backups(backup_dir: Path, keep_days: int, now: datetime | None = None,
                  dry_run: bool = False) -> list[Path]:
    """Hapus backup yang lebih tua dari keep_days (berdasarkan timestamp di nama file)."""
    if keep_days < 1:
        raise ConfigError("--keep-days minimal 1")
    cutoff = (now or now_wib()) - timedelta(days=keep_days)
    old = [p for p, ts in find_backups(backup_dir) if ts < cutoff]
    if not dry_run:
        for p in old:
            p.unlink()
    return old


# ── CLI ───────────────────────────────────────────────────

def main(argv: list[str] | None = None, database_url: str | None = None,
         backup_dir: Path | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backup database SQLite RADsoftBOT ke folder backups/.")
    ap.add_argument("--keep-days", type=int, default=DEFAULT_KEEP,
                    help=f"hapus backup yang lebih tua dari N hari (default {DEFAULT_KEEP})")
    ap.add_argument("--dry-run", action="store_true",
                    help="tampilkan apa yang akan dibuat/dihapus tanpa mengubah apa pun")
    args = ap.parse_args(argv)

    backup_dir = backup_dir or BACKUP_DIR
    try:
        if args.keep_days < 1:
            raise ConfigError("--keep-days minimal 1")
        db_path = sqlite_path_from_url(database_url or DATABASE_URL)

        if args.dry_run:
            if not db_path.is_file():
                raise ConfigError(f"File database tidak ditemukan: {db_path}")
            print(f"[dry-run] Akan membuat backup dari {db_path.name} ke {backup_dir}")
        else:
            out = create_backup(db_path, backup_dir)
            print(f"Backup dibuat: {out}  (integrity_check: ok, {out.stat().st_size:,} byte)")

        removed = prune_backups(backup_dir, args.keep_days, dry_run=args.dry_run)
        label = "[dry-run] Akan dihapus" if args.dry_run else "Dihapus"
        if removed:
            for p in removed:
                print(f"{label} (> {args.keep_days} hari): {p.name}")
        else:
            print(f"Tidak ada backup yang lebih tua dari {args.keep_days} hari.")
        return EXIT_OK

    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_CONFIG
    except BackupError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
