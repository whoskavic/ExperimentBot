import asyncio
import logging
import os
import discord
import openpyxl
from datetime import datetime, date, time as dtime, timedelta

from config import TZ, USER_MAP, CHANNEL_IDS, THREAD_IDS, DESTINATION_CHANNEL
from services.recap_store import SourceInfo, save_recap_range

log = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────

def resolve_name(author) -> str:
    return USER_MAP.get(str(author.id), author.display_name).upper()


def _start_of_day(d: date) -> datetime:
    return TZ.localize(datetime.combine(d, dtime.min))


def _end_of_day(d: date) -> datetime:
    return TZ.localize(datetime.combine(d, dtime(23, 59, 59)))


# ── time boundaries ───────────────────────────────────────

_OPEN_LATE   = dtime(10, 0, 0)   # opening dianggap telat jika > 10:00
_CLOSE_START = dtime(10, 0, 0)   # closing hanya valid mulai jam 10:00
_CLOSE_LATE  = dtime(20, 0, 0)   # closing dianggap telat jika > 20:00


# ── single source, full date range (1 API call) ───────────

# daily structure: {date: {uid: {"name": str, "op": bool, "cl": bool, "op_late": bool, "cl_late": bool}}}
# Kunci = Discord user id, supaya dua user dengan nama sama tidak tergabung.
type DayData = dict[int, dict]

async def _collect_range(source, date_from: date, date_to: date) -> dict[date, DayData]:
    """
    Fetch semua pesan dari 1 source untuk seluruh range dalam 1 API call.
    Return: {date: {uid: {name, op, cl, op_late, cl_late}}}
    """
    daily: dict[date, DayData] = {}

    after  = _start_of_day(date_from)
    before = _end_of_day(date_to)

    async for msg in source.history(after=after, before=before, limit=None):
        msg_time = msg.created_at.astimezone(TZ)
        msg_date = msg_time.date()
        t        = msg_time.time()

        if msg.author.bot:
            continue

        if str(msg.author.id) not in USER_MAP:
            continue

        lines = msg.content.splitlines()
        if not lines:
            continue

        header = lines[0].strip().upper()

        is_open  = "OPENING" in header or "OPEN" in header
        is_close = ("CLOSING" in header or "CLOSE" in header) and t >= _CLOSE_START

        if not is_open and not is_close:
            continue

        uid = msg.author.id

        if msg_date not in daily:
            daily[msg_date] = {}
        if uid not in daily[msg_date]:
            daily[msg_date][uid] = {"name": resolve_name(msg.author),
                                    "op": False, "cl": False, "op_late": False, "cl_late": False}

        entry = daily[msg_date][uid]

        if is_open:
            if t > _OPEN_LATE:
                entry["op_late"] = True
            else:
                entry["op"] = True

        if is_close:
            if t > _CLOSE_LATE:
                entry["cl_late"] = True
            else:
                entry["cl"] = True

    return daily


# ── write xlsx for 1 source, date range ──────────────────

def _write_xlsx(source_name: str, daily: dict[date, DayData], date_from: date, date_to: date) -> str:
    """
    Tulis .xlsx untuk 1 source. Format & urutan sama seperti sebelumnya:
    per tanggal, diurutkan berdasarkan nama (uid sebagai penentu urutan
    kalau ada dua nama yang sama → tetap dua baris).
    Return: filepath string.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Recap"
    ws.append(["date", "source", "user", "opening", "opening_late", "closing", "closing_late"])

    current = date_from
    while current <= date_to:
        day_data = daily.get(current, {})
        for uid in sorted(day_data, key=lambda u: (day_data[u]["name"], u)):
            e = day_data[uid]
            ws.append([
                str(current),
                source_name,
                e["name"],
                1 if e["op"] else 0,
                1 if e["op_late"] else 0,
                1 if e["cl"] else 0,
                1 if e["cl_late"] else 0,
            ])
        current += timedelta(days=1)

    safe_name = source_name.replace(" ", "_")
    label = f"{date_from}" if date_from == date_to else f"{date_from}_to_{date_to}"
    filename = f"recap_{label}_{safe_name}.xlsx"
    wb.save(filename)
    return filename


async def generate_recap(source, date_from: date, date_to: date) -> str:
    """
    Generate .xlsx recap untuk 1 source (channel/thread),
    dari date_from sampai date_to (inklusif). Tanpa simpan ke DB.
    Return: filepath string.
    """
    daily = await _collect_range(source, date_from, date_to)
    return _write_xlsx(source.name, daily, date_from, date_to)


# ── save to database ──────────────────────────────────────

async def _save_to_db(source, kind: str, daily: dict[date, DayData],
                      date_from: date, date_to: date, triggered_by: str) -> dict:
    """
    Simpan hasil 1 source ke DB di thread terpisah (tidak memblokir event loop).
    Tidak pernah raise: kegagalan dilaporkan sebagai status "error" per tanggal,
    supaya pengiriman Excel ke Discord tetap jalan.
    """
    report = {"source_id": str(source.id), "source": source.name, "kind": kind, "dates": []}
    try:
        info = SourceInfo(discord_id=source.id, kind=kind, name=source.name)
        results = await asyncio.to_thread(
            save_recap_range, info, date_from, date_to, daily, triggered_by
        )
        report["dates"] = [r.to_dict() for r in results]
    except Exception as exc:
        log.exception("Gagal menyimpan rekap %s (%s) ke database", source.name, source.id)
        msg = f"{type(exc).__name__}: {exc}"
        d = date_from
        while d <= date_to:
            report["dates"].append({"recap_date": d.isoformat(), "status": "error", "recap_id": None,
                                    "revision_no": None, "names_updated": 0, "error": msg})
            d += timedelta(days=1)
    return report


async def _process_source(source, kind: str, date_from: date, date_to: date,
                          triggered_by: str) -> tuple[str, dict]:
    daily    = await _collect_range(source, date_from, date_to)
    report   = await _save_to_db(source, kind, daily, date_from, date_to, triggered_by)
    filepath = _write_xlsx(source.name, daily, date_from, date_to)
    return filepath, report


# ── run all sources, date range ───────────────────────────

async def run_all_recaps(client, date_from: date, date_to: date,
                         triggered_by: str = "panel") -> tuple[list[tuple[str, object]], list[dict]]:
    """
    Kumpulkan, simpan ke DB, dan tulis xlsx untuk semua channel + thread.
    Semua source diproses secara paralel.

    Return: (results, save_report)
      results     = [(filepath, source_channel)]  — dipakai send_recaps*
      save_report = [{source_id, source, kind, dates: [{recap_date, status, ...}]}]
    """
    all_ids = [(cid, "channel") for cid in CHANNEL_IDS] +               [(tid, "thread")  for tid in THREAD_IDS]

    sources = []
    for source_id, kind in all_ids:
        source = client.get_channel(source_id)
        if source is None:
            print(f"{kind.capitalize()} {source_id} not found")
            continue
        sources.append((source, kind))

    if not sources:
        return [], []

    processed = await asyncio.gather(
        *[_process_source(src, kind, date_from, date_to, triggered_by) for src, kind in sources]
    )

    results     = [(filepath, src) for (filepath, _), (src, _) in zip(processed, sources)]
    save_report = [report for _, report in processed]
    return results, save_report


# ── build all_channel.xlsx ────────────────────────────────

def build_all_channel_xlsx(results: list[tuple[str, object]], date_from: date, date_to: date) -> str:
    wb_all = openpyxl.Workbook()
    ws_all = wb_all.active
    ws_all.title = "All Channels"
    ws_all.append(["date", "source", "user", "opening", "opening_late", "closing", "closing_late"])

    for filepath, _ in results:
        wb = openpyxl.load_workbook(filepath)
        ws = wb.active
        for row in ws.iter_rows(min_row=2, values_only=True):
            ws_all.append(list(row))

    label = f"{date_from}" if date_from == date_to else f"{date_from}_to_{date_to}"
    all_filename = f"recap_{label}_all_channel.xlsx"
    wb_all.save(all_filename)
    return all_filename


# ── send to Discord & cleanup ─────────────────────────────

async def send_recaps(dest, results: list[tuple[str, object]], date_from: date, date_to: date) -> list[str]:
    """Kirim semua file ke dest channel (manual/API), hapus file lokal setelah kirim."""
    label = f"`{date_from}`" if date_from == date_to else f"`{date_from}` s/d `{date_to}`"

    all_filename = build_all_channel_xlsx(results, date_from, date_to)

    sent: list[str] = []

    for filepath, source in results:
        await dest.send(
            f"Recap {label} — **#{source.name}**",
            file=discord.File(filepath)
        )
        sent.append(filepath)
        os.remove(filepath)

    await dest.send(
        f"Recap {label} — **All Channels**",
        file=discord.File(all_filename)
    )
    sent.append(all_filename)
    os.remove(all_filename)

    return sent


async def send_recaps_scheduled(dest, results: list[tuple[str, object]], date_from: date, date_to: date) -> None:
    """
    Kirim recap scheduler: tiap file ke source channel-nya masing-masing,
    all_channel dikirim ke dest (destination_channel).
    """
    label = f"`{date_from}`" if date_from == date_to else f"`{date_from}` s/d `{date_to}`"

    all_filename = build_all_channel_xlsx(results, date_from, date_to)

    for filepath, source in results:
        await source.send(
            f"Recap {label} — **#{source.name}**",
            file=discord.File(filepath)
        )
        os.remove(filepath)

    await dest.send(
        f"Recap {label} — **All Channels**",
        file=discord.File(all_filename)
    )
    os.remove(all_filename)