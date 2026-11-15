"""
Simpan / rewrite rekap ke database.

Semua fungsi di sini SINKRON. Dari event loop discord.py panggil lewat
`asyncio.to_thread(...)` supaya tidak memblokir bot.

Aturan (lihat docs/PROGRESS-database-login.md):
- Unit = (source, tanggal WIB). Rekap ulang menimpa, tidak menduplikasi.
- Yang dibandingkan hanya 4 flag per discord_user_id. Perubahan display_name
  saja → nama diperbarui, status "unchanged", TANPA revisi.
- Berbeda → entries lama disimpan sebagai snapshot di recap_revisions dulu.
- Tanggal tanpa data → recaps dengan 0 entries.
- Tanggal > hari ini (WIB) → tidak disimpan ("skipped").
- Rentang tanggal → satu transaksi per tanggal; gagal satu tidak membatalkan
  yang lain ("error").
"""
import logging
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta

from sqlalchemy import select

from config import TZ
from db.base import session_scope, utcnow
from db.models import Source, Recap, RecapEntry, RecapRevision

log = logging.getLogger(__name__)

FLAGS = ("opening", "opening_late", "closing", "closing_late")

CREATED   = "created"
UPDATED   = "updated"
UNCHANGED = "unchanged"
SKIPPED   = "skipped"
ERROR     = "error"


# ── data in / out ─────────────────────────────────────────

@dataclass(frozen=True)
class SourceInfo:
    discord_id: int
    kind: str          # 'channel' | 'thread'
    name: str


@dataclass(frozen=True)
class EntryIn:
    discord_user_id: int
    display_name: str
    opening: bool = False
    opening_late: bool = False
    closing: bool = False
    closing_late: bool = False

    def flags(self) -> tuple[bool, ...]:
        return tuple(bool(getattr(self, f)) for f in FLAGS)


@dataclass
class DaySaveResult:
    recap_date: date
    status: str
    recap_id: int | None = None
    revision_no: int | None = None     # terisi kalau status "updated"
    names_updated: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["recap_date"] = self.recap_date.isoformat()
        return d


def today_wib() -> date:
    return datetime.now(TZ).date()


def entries_from_day_data(day_data: dict) -> list[EntryIn]:
    """
    Konversi 1 hari hasil `_collect_range`:
        {uid: {"name": str, "op": bool, "op_late": bool, "cl": bool, "cl_late": bool}}
    → list[EntryIn]. Kunci = Discord user id, jadi dua user dengan nama
    sama tetap menjadi dua entry.
    """
    out: list[EntryIn] = []
    for uid, e in day_data.items():
        if not e.get("name"):
            raise ValueError(f"entry uid={uid} tidak punya 'name'")
        out.append(EntryIn(
            discord_user_id=int(uid),
            display_name=e["name"],
            opening=bool(e.get("op")),
            opening_late=bool(e.get("op_late")),
            closing=bool(e.get("cl")),
            closing_late=bool(e.get("cl_late")),
        ))
    return out


# ── helpers ───────────────────────────────────────────────

def _flag_map(entries) -> dict[int, tuple[bool, ...]]:
    return {int(e.discord_user_id): tuple(bool(getattr(e, f)) for f in FLAGS) for e in entries}


def _snapshot(recap: Recap) -> dict:
    # discord_user_id disimpan sebagai string: > 2^53, aman dibaca JavaScript.
    return {
        "revision_count": recap.revision_count,
        "triggered_by":   recap.last_triggered_by,
        "updated_at":     recap.updated_at.isoformat() if recap.updated_at else None,
        "entries": [
            {"discord_user_id": str(e.discord_user_id), "display_name": e.display_name,
             **{f: bool(getattr(e, f)) for f in FLAGS}}
            for e in sorted(recap.entries, key=lambda x: x.discord_user_id)
        ],
    }


def _upsert_source(s, info: SourceInfo) -> Source:
    src = s.scalars(select(Source).where(Source.discord_id == info.discord_id)).one_or_none()
    if src is None:
        src = Source(discord_id=info.discord_id, kind=info.kind, name=info.name)
        s.add(src)
        s.flush()
    else:
        if src.name != info.name:
            src.name = info.name
        if src.kind != info.kind:
            src.kind = info.kind
    return src


def _new_entry(recap: Recap, e: EntryIn) -> RecapEntry:
    return RecapEntry(recap=recap, discord_user_id=e.discord_user_id, display_name=e.display_name,
                      **{f: bool(getattr(e, f)) for f in FLAGS})


# ── public API ────────────────────────────────────────────

def save_recap_day(source: SourceInfo, recap_date: date, entries: list[EntryIn],
                   triggered_by: str, today: date | None = None) -> DaySaveResult:
    """Simpan / rewrite rekap 1 source untuk 1 tanggal dalam 1 transaksi."""
    today = today or today_wib()
    if recap_date > today:
        return DaySaveResult(recap_date, SKIPPED)

    uids = [e.discord_user_id for e in entries]
    if len(uids) != len(set(uids)):
        raise ValueError(f"discord_user_id duplikat untuk {recap_date}")

    new_flags = _flag_map(entries)
    now = utcnow()

    with session_scope(write=True) as s:
        src   = _upsert_source(s, source)
        recap = s.scalars(
            select(Recap).where(Recap.source_id == src.id, Recap.recap_date == recap_date)
        ).one_or_none()

        # 1. belum ada → buat
        if recap is None:
            recap = Recap(source=src, recap_date=recap_date, revision_count=0,
                          last_triggered_by=triggered_by, created_at=now, updated_at=now)
            s.add(recap)
            for e in entries:
                s.add(_new_entry(recap, e))
            s.flush()
            return DaySaveResult(recap_date, CREATED, recap_id=recap.id)

        # 2. flag identik → hanya perbarui nama / updated_at / last_triggered_by
        if _flag_map(recap.entries) == new_flags:
            new_names = {e.discord_user_id: e.display_name for e in entries}
            renamed = 0
            for old in recap.entries:
                name = new_names[old.discord_user_id]
                if old.display_name != name:
                    old.display_name = name
                    renamed += 1
            recap.updated_at = now
            recap.last_triggered_by = triggered_by
            return DaySaveResult(recap_date, UNCHANGED, recap_id=recap.id, names_updated=renamed)

        # 3. berbeda → snapshot dulu, lalu ganti entries
        revision_no = recap.revision_count + 1
        s.add(RecapRevision(recap=recap, revision_no=revision_no, snapshot=_snapshot(recap),
                            triggered_by=triggered_by, created_at=now))
        for old in list(recap.entries):
            s.delete(old)
        s.flush()   # DELETE harus jalan sebelum INSERT (UNIQUE recap_id + discord_user_id)
        s.expire(recap, ["entries"])

        for e in entries:
            s.add(_new_entry(recap, e))
        recap.revision_count = revision_no
        recap.updated_at = now
        recap.last_triggered_by = triggered_by
        return DaySaveResult(recap_date, UPDATED, recap_id=recap.id, revision_no=revision_no)


def save_recap_range(source: SourceInfo, date_from: date, date_to: date,
                     daily: dict[date, dict], triggered_by: str,
                     today: date | None = None) -> list[DaySaveResult]:
    """
    Simpan setiap tanggal dalam [date_from, date_to] (inklusif), masing-masing
    dalam transaksinya sendiri. Tanggal tanpa data → 0 entries.
    """
    today = today or today_wib()
    results: list[DaySaveResult] = []

    d = date_from
    while d <= date_to:
        try:
            entries = entries_from_day_data(daily.get(d, {}))
            results.append(save_recap_day(source, d, entries, triggered_by, today))
        except Exception as exc:
            log.exception("Gagal menyimpan rekap %s (%s) tanggal %s", source.name, source.discord_id, d)
            results.append(DaySaveResult(d, ERROR, error=f"{type(exc).__name__}: {exc}"))
        d += timedelta(days=1)

    return results
