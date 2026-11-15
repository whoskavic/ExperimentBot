"""
Langkah 5: _collect_range (kunci uid), _write_xlsx, run_all_recaps + DB.

Semua data di file ini FIKTIF (repo public).
"""
import asyncio
import os
from datetime import date, datetime, time as dtime, timedelta
from types import SimpleNamespace

import openpyxl
import pytz
import pytest
from sqlalchemy import select

import services.recap as recap
from config import TZ
from db.base import session_scope
from db.models import Recap, RecapEntry, Source

D1, D2 = date(2026, 11, 12), date(2026, 11, 13)

U_ANDI, U_SITI, U_RINA, U_BUDI1, U_BUDI2, U_OUTSIDER = (
    900000000000000011, 900000000000000012, 900000000000000013,
    900000000000000014, 900000000000000015, 900000000000000099,
)
USERS = {
    str(U_ANDI): "Andi Wijaya",
    str(U_SITI): "SITI",
    str(U_RINA): "rina",
}
USERS_DUP = {**USERS, str(U_BUDI1): "BUDI", str(U_BUDI2): "Budi"}   # 2 uid → nama "BUDI"


# ── fakes ─────────────────────────────────────────────────

def msg(uid, d, hh, mm, content, bot=False, display_name="discord_nick"):
    # discord.py memberi created_at dalam UTC (aware)
    created = TZ.localize(datetime.combine(d, dtime(hh, mm))).astimezone(pytz.utc)
    return SimpleNamespace(
        author=SimpleNamespace(id=uid, bot=bot, display_name=display_name),
        content=content,
        created_at=created,
    )


class FakeSource:
    def __init__(self, sid, name, messages):
        self.id, self.name, self._messages = sid, name, messages
        self.sent = []

    async def history(self, after, before, limit=None):
        for m in self._messages:
            if after < m.created_at < before:
                yield m

    async def send(self, text, file=None):
        self.sent.append(text)


class FakeClient:
    def __init__(self, sources):
        self._by_id = {s.id: s for s in sources}

    def get_channel(self, cid):
        return self._by_id.get(cid)


def scenario_messages():
    """Campuran: tepat waktu, telat, closing < 10:00 (diabaikan), bot, user luar, pesan kosong, 2 hari."""
    return [
        msg(U_ANDI, D1, 8, 30, "OPENING\n- task 1"),
        msg(U_ANDI, D1, 17, 0, "Closing day\n- done"),
        msg(U_SITI, D1, 10, 15, "open"),                   # opening telat
        msg(U_SITI, D1, 9, 0, "CLOSE"),                    # closing < 10:00 → diabaikan
        msg(U_SITI, D1, 20, 30, "CLOSING"),                # closing telat
        msg(U_RINA, D1, 9, 59, "  opening day  "),
        msg(U_RINA, D1, 12, 0, "makan siang"),             # bukan open/close
        msg(U_OUTSIDER, D1, 8, 0, "OPENING"),              # tidak terdaftar
        msg(U_ANDI, D1, 8, 0, "OPENING", bot=True),        # bot
        msg(U_RINA, D1, 8, 0, ""),                         # kosong
        msg(U_ANDI, D2, 11, 0, "OPENING"),
        msg(U_RINA, D2, 19, 59, "CLOSING"),
        msg(U_SITI, D2, 0, 0, "OPENING"),                  # tepat tengah malam WIB
    ]


def xlsx_rows(path):
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    return ws.title, [list(r) for r in ws.iter_rows(values_only=True)]


# ── salinan PERSIS kode lama (commit d2a69f8) sebagai pembanding ──

async def _legacy_collect_range(source, date_from, date_to):
    daily = {}
    after  = recap._start_of_day(date_from)
    before = recap._end_of_day(date_to)
    async for m in source.history(after=after, before=before, limit=None):
        msg_time = m.created_at.astimezone(TZ)
        msg_date = msg_time.date()
        t        = msg_time.time()
        if m.author.bot:
            continue
        if str(m.author.id) not in recap.USER_MAP:
            continue
        lines = m.content.splitlines()
        if not lines:
            continue
        header = lines[0].strip().upper()
        is_open  = "OPENING" in header or "OPEN" in header
        is_close = ("CLOSING" in header or "CLOSE" in header) and t >= recap._CLOSE_START
        if not is_open and not is_close:
            continue
        user = recap.resolve_name(m.author)
        if msg_date not in daily:
            daily[msg_date] = {}
        if user not in daily[msg_date]:
            daily[msg_date][user] = {"op": False, "cl": False, "op_late": False, "cl_late": False}
        entry = daily[msg_date][user]
        if is_open:
            if t > recap._OPEN_LATE:
                entry["op_late"] = True
            else:
                entry["op"] = True
        if is_close:
            if t > recap._CLOSE_LATE:
                entry["cl_late"] = True
            else:
                entry["cl"] = True
    return daily


async def _legacy_generate_recap(source, date_from, date_to):
    daily = await _legacy_collect_range(source, date_from, date_to)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Recap"
    ws.append(["date", "source", "user", "opening", "opening_late", "closing", "closing_late"])
    current = date_from
    while current <= date_to:
        day_data = daily.get(current, {})
        for u in sorted(day_data):
            e = day_data[u]
            ws.append([str(current), source.name, u,
                       1 if e["op"] else 0, 1 if e["op_late"] else 0,
                       1 if e["cl"] else 0, 1 if e["cl_late"] else 0])
        current += timedelta(days=1)
    source_name = source.name.replace(" ", "_")
    label = f"{date_from}" if date_from == date_to else f"{date_from}_to_{date_to}"
    filename = f"recap_{label}_{source_name}.xlsx"
    wb.save(filename)
    return filename


# ── fixtures ──────────────────────────────────────────────

@pytest.fixture
def users(monkeypatch):
    monkeypatch.setattr(recap, "USER_MAP", USERS)


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ── _collect_range: kunci uid ─────────────────────────────

def test_collect_range_keyed_by_uid_with_name_field(users):
    src = FakeSource(800000000000000001, "opening closing", scenario_messages())
    daily = asyncio.run(recap._collect_range(src, D1, D2))

    assert daily[D1] == {
        U_ANDI: {"name": "ANDI WIJAYA", "op": True,  "cl": True,  "op_late": False, "cl_late": False},
        U_SITI: {"name": "SITI",        "op": False, "cl": False, "op_late": True,  "cl_late": True},
        U_RINA: {"name": "RINA",        "op": True,  "cl": False, "op_late": False, "cl_late": False},
    }
    assert daily[D2] == {
        U_ANDI: {"name": "ANDI WIJAYA", "op": False, "cl": False, "op_late": True,  "cl_late": False},
        U_RINA: {"name": "RINA",        "op": False, "cl": True,  "op_late": False, "cl_late": False},
        U_SITI: {"name": "SITI",        "op": True,  "cl": False, "op_late": False, "cl_late": False},
    }


def test_two_uids_same_name_are_not_merged(monkeypatch, in_tmp):
    monkeypatch.setattr(recap, "USER_MAP", USERS_DUP)
    src = FakeSource(800000000000000001, "general", [
        msg(U_BUDI1, D1, 8, 0, "OPENING"),
        msg(U_BUDI2, D1, 18, 0, "CLOSING"),
    ])
    daily = asyncio.run(recap._collect_range(src, D1, D1))
    assert daily[D1] == {
        U_BUDI1: {"name": "BUDI", "op": True,  "cl": False, "op_late": False, "cl_late": False},
        U_BUDI2: {"name": "BUDI", "op": False, "cl": True,  "op_late": False, "cl_late": False},
    }

    # Excel: dua baris "BUDI", urut uid
    _, rows = xlsx_rows(recap._write_xlsx(src.name, daily, D1, D1))
    assert rows[1:] == [
        ["2026-11-12", "general", "BUDI", 1, 0, 0, 0],
        ["2026-11-12", "general", "BUDI", 0, 0, 1, 0],
    ]


# ── Excel identik dengan versi lama ───────────────────────

@pytest.mark.parametrize("source_name, date_from, date_to", [
    ("opening closing", D1, D1),       # 1 hari, nama dengan spasi
    ("thread-dev",      D1, D2),       # rentang
    ("kosong",          date(2026, 11, 1), date(2026, 11, 3)),   # tanpa data
])
def test_xlsx_identical_to_legacy(users, in_tmp, source_name, date_from, date_to):
    src = FakeSource(800000000000000001, source_name, scenario_messages())

    legacy_path = asyncio.run(_legacy_generate_recap(src, date_from, date_to))
    os.rename(legacy_path, "legacy.xlsx")
    new_path = asyncio.run(recap.generate_recap(src, date_from, date_to))

    assert new_path == legacy_path                       # nama file sama
    assert xlsx_rows(new_path) == xlsx_rows("legacy.xlsx")   # judul sheet + semua sel sama
    if date_from == D1 and date_to == D2:
        assert len(xlsx_rows(new_path)[1]) == 7          # header + 6 baris (memastikan data tidak kosong)


def test_all_channel_xlsx_identical_to_legacy(users, in_tmp):
    srcs = [FakeSource(800000000000000001, "channel a", scenario_messages()),
            FakeSource(800000000000000002, "thread b", scenario_messages()[:6])]

    legacy = []
    for s in srcs:
        p = asyncio.run(_legacy_generate_recap(s, D1, D2))
        os.rename(p, f"legacy_{p}")
        legacy.append((f"legacy_{p}", s))
    legacy_all = recap.build_all_channel_xlsx(legacy, D1, D2)
    os.rename(legacy_all, "legacy_all.xlsx")

    new = [(asyncio.run(recap.generate_recap(s, D1, D2)), s) for s in srcs]
    new_all = recap.build_all_channel_xlsx(new, D1, D2)

    assert xlsx_rows(new_all) == xlsx_rows("legacy_all.xlsx")


# ── run_all_recaps: Excel + simpan DB ─────────────────────

def _client_and_config(monkeypatch, messages_a, messages_b):
    a = FakeSource(800000000000000001, "channel a", messages_a)
    b = FakeSource(800000000000000002, "thread b", messages_b)
    monkeypatch.setattr(recap, "CHANNEL_IDS", [a.id, 800000000000000003])   # 1 tidak ditemukan
    monkeypatch.setattr(recap, "THREAD_IDS", [b.id])
    return FakeClient([a, b]), a, b


def test_run_all_recaps_writes_xlsx_and_saves_to_db(db, users, in_tmp, monkeypatch):
    client, a, b = _client_and_config(monkeypatch, scenario_messages(), [])
    results, report = asyncio.run(recap.run_all_recaps(client, D1, D2, triggered_by="scheduler"))

    # results tetap [(filepath, source)] seperti sebelumnya
    assert [(os.path.exists(p), s) for p, s in results] == [(True, a), (True, b)]

    assert [(r["source_id"], r["source"], r["kind"]) for r in report] == [
        (str(a.id), "channel a", "channel"), (str(b.id), "thread b", "thread")]
    assert [[d["status"] for d in r["dates"]] for r in report] == [
        ["created", "created"], ["created", "created"]]

    with session_scope() as s:
        srcs = {x.discord_id: x.kind for x in s.scalars(select(Source))}
        assert srcs == {a.id: "channel", b.id: "thread"}
        rows = s.execute(
            select(Source.discord_id, Recap.recap_date, RecapEntry.discord_user_id, RecapEntry.display_name)
            .join(Recap, Recap.source_id == Source.id).join(RecapEntry)
            .where(Recap.recap_date == D1)
        ).all()
        assert sorted(rows) == sorted([
            (a.id, D1, U_ANDI, "ANDI WIJAYA"), (a.id, D1, U_SITI, "SITI"), (a.id, D1, U_RINA, "RINA")])
        assert all(r.last_triggered_by == "scheduler" for r in s.scalars(select(Recap)))
        assert len(s.scalars(select(Recap).where(Recap.source_id ==
                   s.scalar(select(Source.id).where(Source.discord_id == b.id)))).all()) == 2  # 0 entries tetap disimpan

    # rekap ulang data sama → unchanged
    _, report2 = asyncio.run(recap.run_all_recaps(client, D1, D2, triggered_by="panel"))
    assert [[d["status"] for d in r["dates"]] for r in report2] == [
        ["unchanged", "unchanged"], ["unchanged", "unchanged"]]


def test_db_failure_still_produces_xlsx(db, users, in_tmp, monkeypatch, caplog):
    client, a, b = _client_and_config(monkeypatch, scenario_messages(), [])

    def boom(*a, **kw):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(recap, "save_recap_range", boom)

    results, report = asyncio.run(recap.run_all_recaps(client, D1, D1))
    assert all(os.path.exists(p) for p, _ in results)
    assert [d["status"] for r in report for d in r["dates"]] == ["error", "error"]
    assert report[0]["dates"][0]["error"] == "RuntimeError: database is locked"
    assert "Gagal menyimpan rekap" in caplog.text


def test_run_all_recaps_no_sources(monkeypatch):
    monkeypatch.setattr(recap, "CHANNEL_IDS", [1])
    monkeypatch.setattr(recap, "THREAD_IDS", [])
    assert asyncio.run(recap.run_all_recaps(FakeClient([]), D1, D1)) == ([], [])


def test_db_save_runs_off_event_loop(db, users, in_tmp, monkeypatch):
    """save_recap_range harus jalan di thread lain, bukan di thread event loop."""
    import threading
    client, *_ = _client_and_config(monkeypatch, scenario_messages(), [])
    seen = {}
    real = recap.save_recap_range

    def spy(*a, **kw):
        seen.setdefault("threads", set()).add(threading.get_ident())
        return real(*a, **kw)
    monkeypatch.setattr(recap, "save_recap_range", spy)

    async def main():
        seen["loop_thread"] = threading.get_ident()
        return await recap.run_all_recaps(client, D1, D1)

    asyncio.run(main())
    assert seen["threads"] and seen["loop_thread"] not in seen["threads"]
