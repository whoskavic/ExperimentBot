"""
Sisi BACA rekap: riwayat (filter + pagination), detail + revisi, export Excel.

- Timestamp di DB = UTC naive. Semua yang keluar ke panel / Excel dikonversi
  ke WIB (Asia/Jakarta) dengan label "WIB".
- Rentang tanggal dibatasi MAX_RANGE_DAYS hari (inklusif).
- discord id dikirim sebagai string (> 2^53, aman untuk JavaScript).
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import BytesIO

import openpyxl
import pytz
from sqlalchemy import func, select

from config import TZ
from db.base import session_scope
from db.models import Recap, RecapEntry, RecapRevision, Source
from services.recap_store import FLAGS, today_wib

MAX_RANGE_DAYS   = 366
DEFAULT_DAYS     = 30
DEFAULT_PER_PAGE = 25
MAX_PER_PAGE     = 100
NO_TEAM          = "__none__"      # filter: source tanpa tim

# Kolom sama persis dengan Excel yang dikirim ke Discord (services/recap.py).
XLSX_HEADER = ["date", "source", "user", "opening", "opening_late", "closing", "closing_late"]


class FilterError(ValueError):
    """Parameter filter tidak valid → 400 di API."""


# ── waktu ─────────────────────────────────────────────────

def to_wib(dt: datetime | None) -> str | None:
    """UTC naive (dari DB) → 'YYYY-MM-DD HH:MM:SS WIB'."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S") + " WIB"


def _iso_to_wib(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return to_wib(datetime.fromisoformat(raw))
    except ValueError:
        return raw


# ── filter ────────────────────────────────────────────────

@dataclass(frozen=True)
class RecapFilter:
    date_from: date
    date_to: date
    source_id: int | None = None
    team: str | None = None          # nama tim, atau NO_TEAM

    @property
    def label(self) -> str:
        return f"{self.date_from}" if self.date_from == self.date_to else f"{self.date_from}_to_{self.date_to}"


def _parse_date(name: str, raw: str | None) -> date | None:
    if raw is None or raw.strip() == "":
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        raise FilterError(f"Format {name} salah (harus YYYY-MM-DD): {raw!r}")


def parse_int(name: str, raw: str | None, default: int | None = None, minimum: int = 1) -> int | None:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise FilterError(f"{name} harus angka: {raw!r}")
    if value < minimum:
        raise FilterError(f"{name} minimal {minimum}")
    return value


def parse_filter(args, today: date | None = None) -> RecapFilter:
    """
    Dari query string. Aturan tanggal:
    - keduanya kosong → DEFAULT_DAYS hari terakhir (s/d hari ini WIB)
    - salah satu saja → dianggap satu tanggal itu
    - date_from > date_to atau rentang > MAX_RANGE_DAYS → FilterError
    """
    today = today or today_wib()
    d_from = _parse_date("date_from", args.get("date_from"))
    d_to   = _parse_date("date_to", args.get("date_to"))

    if d_from is None and d_to is None:
        d_to, d_from = today, today - timedelta(days=DEFAULT_DAYS - 1)
    elif d_from is None:
        d_from = d_to
    elif d_to is None:
        d_to = d_from

    if d_from > d_to:
        raise FilterError("date_from tidak boleh lebih besar dari date_to")
    if (d_to - d_from).days + 1 > MAX_RANGE_DAYS:
        raise FilterError(f"Rentang tanggal maksimal {MAX_RANGE_DAYS} hari")

    team = (args.get("team") or "").strip() or None
    return RecapFilter(d_from, d_to, parse_int("source_id", args.get("source_id")), team)


def _apply_filter(stmt, f: RecapFilter):
    stmt = stmt.where(Recap.recap_date >= f.date_from, Recap.recap_date <= f.date_to)
    if f.source_id is not None:
        stmt = stmt.where(Recap.source_id == f.source_id)
    if f.team == NO_TEAM:
        stmt = stmt.where(Source.team.is_(None))
    elif f.team:
        stmt = stmt.where(Source.team == f.team)
    return stmt


# ── serialisasi ───────────────────────────────────────────

def _source_dict(src: Source) -> dict:
    return {"id": src.id, "discord_id": str(src.discord_id), "kind": src.kind,
            "name": src.name, "team": src.team}


def _recap_dict(r: Recap, entry_count: int) -> dict:
    return {
        "id": r.id,
        "recap_date": r.recap_date.isoformat(),
        "source": _source_dict(r.source),
        "entry_count": entry_count,
        "revision_count": r.revision_count,
        "last_triggered_by": r.last_triggered_by,
        "created_at": to_wib(r.created_at),
        "updated_at": to_wib(r.updated_at),
    }


def _entry_dict(e) -> dict:
    return {"discord_user_id": str(e.discord_user_id), "display_name": e.display_name,
            **{f: bool(getattr(e, f)) for f in FLAGS}}


# ── public API ────────────────────────────────────────────

def list_sources() -> dict:
    with session_scope() as s:
        sources = s.scalars(select(Source).order_by(Source.name, Source.id)).all()
        teams = sorted({x.team for x in sources if x.team})
        return {"sources": [_source_dict(x) for x in sources], "teams": teams}


def list_recaps(f: RecapFilter, page: int = 1, per_page: int = DEFAULT_PER_PAGE) -> dict:
    per_page = max(1, min(per_page, MAX_PER_PAGE))
    page = max(1, page)

    entry_count = (
        select(RecapEntry.recap_id, func.count(RecapEntry.id).label("n"))
        .group_by(RecapEntry.recap_id).subquery()
    )
    base = _apply_filter(select(Recap).join(Source), f)

    with session_scope() as s:
        total = s.scalar(select(func.count()).select_from(base.subquery()))
        rows = s.execute(
            _apply_filter(
                select(Recap, func.coalesce(entry_count.c.n, 0))
                .join(Source)
                .outerjoin(entry_count, entry_count.c.recap_id == Recap.id), f)
            .order_by(Recap.recap_date.desc(), Source.name, Source.id)
            .offset((page - 1) * per_page).limit(per_page)
        ).all()
        items = [_recap_dict(r, n) for r, n in rows]

    return {
        "items": items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": max(1, -(-total // per_page)),
        "filter": {"date_from": f.date_from.isoformat(), "date_to": f.date_to.isoformat(),
                   "source_id": f.source_id, "team": f.team},
    }


def _diff(old_entries: list[dict], new_entries: list[dict]) -> list[dict]:
    """Bandingkan per discord_user_id. status: same | changed | added | removed."""
    old = {e["discord_user_id"]: e for e in old_entries}
    new = {e["discord_user_id"]: e for e in new_entries}
    rows = []
    for uid in sorted(old.keys() | new.keys(),
                      key=lambda u: ((new.get(u) or old.get(u))["display_name"], int(u))):
        o, n = old.get(uid), new.get(uid)
        if o is None:
            status = "added"
        elif n is None:
            status = "removed"
        elif any(o[f] != n[f] for f in FLAGS):
            status = "changed"
        else:
            status = "same"
        rows.append({
            "discord_user_id": uid,
            "display_name": (n or o)["display_name"],
            "old": {f: o[f] for f in FLAGS} if o else None,
            "new": {f: n[f] for f in FLAGS} if n else None,
            "status": status,
        })
    return rows


def get_recap_detail(recap_id: int) -> dict | None:
    """Rekap + entries sekarang + setiap revisi (snapshot lama vs data sekarang)."""
    with session_scope() as s:
        r = s.get(Recap, recap_id)
        if r is None:
            return None
        current = [_entry_dict(e) for e in sorted(r.entries, key=lambda e: (e.display_name, e.discord_user_id))]
        revisions = []
        for v in sorted(r.revisions, key=lambda v: v.revision_no, reverse=True):
            snap = v.snapshot or {}
            old_entries = snap.get("entries", [])
            revisions.append({
                "revision_no": v.revision_no,
                "replaced_by": v.triggered_by,
                "replaced_at": to_wib(v.created_at),
                "old_triggered_by": snap.get("triggered_by"),
                "old_updated_at": _iso_to_wib(snap.get("updated_at")),
                "old_entries": old_entries,
                "diff_vs_current": _diff(old_entries, current),
            })
        return {**_recap_dict(r, len(current)), "entries": current, "revisions": revisions}


def export_xlsx(f: RecapFilter) -> tuple[bytes, str]:
    """
    Excel dari DB, di memori. Sheet "Recap" = kolom sama dengan Excel Discord
    (urut: tanggal, source, nama, uid). Sheet "Info" = filter, waktu export,
    dan metadata tiap rekap (waktu dalam WIB).
    Return: (isi file, nama file)
    """
    with session_scope() as s:
        recaps = s.scalars(
            _apply_filter(select(Recap).join(Source), f)
            .order_by(Recap.recap_date, Source.name, Source.id)
        ).all()

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Recap"
        ws.append(XLSX_HEADER)
        for r in recaps:
            for e in sorted(r.entries, key=lambda e: (e.display_name, e.discord_user_id)):
                ws.append([
                    str(r.recap_date), r.source.name, e.display_name,
                    1 if e.opening else 0, 1 if e.opening_late else 0,
                    1 if e.closing else 0, 1 if e.closing_late else 0,
                ])

        info = wb.create_sheet("Info")
        info.append(["exported_at", to_wib(datetime.now(pytz.utc))])
        info.append(["date_from", str(f.date_from)])
        info.append(["date_to", str(f.date_to)])
        info.append(["source_id", f.source_id if f.source_id is not None else "semua"])
        info.append(["team", "(tanpa tim)" if f.team == NO_TEAM else (f.team or "semua")])
        info.append([])
        info.append(["date", "source", "team", "entries", "revision_count", "last_triggered_by", "updated_at (WIB)"])
        for r in recaps:
            info.append([str(r.recap_date), r.source.name, r.source.team or "", len(r.entries),
                         r.revision_count, r.last_triggered_by, to_wib(r.updated_at)])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue(), f"recap_{f.label}_db.xlsx"
