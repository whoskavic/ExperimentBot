import asyncio
from datetime import date, datetime, timedelta

import pytz
import pytest

from config import TZ
import services.scheduler as sched
from services.scheduler import next_recap_target

UTC = pytz.utc


def wib(y, mo, d, h, mi, s=0):
    return TZ.localize(datetime(y, mo, d, h, mi, s))


# ── next_recap_target ─────────────────────────────────────

@pytest.mark.parametrize("now, expected", [
    # sebelum jadwal → hari yang sama
    (wib(2026, 11, 12, 23, 58, 59), wib(2026, 11, 12, 23, 59)),
    # tepat di jadwal / sesudahnya → besok
    (wib(2026, 11, 12, 23, 59, 0),  wib(2026, 11, 13, 23, 59)),
    (wib(2026, 11, 12, 23, 59, 30), wib(2026, 11, 13, 23, 59)),
    # lewat tengah malam → hari baru itu, bukan kemarin
    (wib(2026, 11, 13, 0, 0, 0),    wib(2026, 11, 13, 23, 59)),
    (wib(2026, 11, 13, 0, 1, 0),    wib(2026, 11, 13, 23, 59)),
    # akhir bulan / akhir tahun
    (wib(2026, 12, 31, 23, 59, 1),  wib(2027, 1, 1, 23, 59)),
])
def test_next_target_around_midnight(now, expected):
    target = next_recap_target(now, 23, 59)
    assert target == expected
    assert target.date() == expected.date()


def test_server_clock_in_utc_uses_wib_date():
    # 16:58 UTC = 23:58 WIB tanggal 12
    assert next_recap_target(UTC.localize(datetime(2026, 11, 12, 16, 58)), 23, 59).date() == date(2026, 11, 12)
    # 17:00:30 UTC = 00:00:30 WIB tanggal 13 (UTC masih tanggal 12)
    assert next_recap_target(UTC.localize(datetime(2026, 11, 12, 17, 0, 30)), 23, 59).date() == date(2026, 11, 13)


def test_woke_slightly_early_does_not_run_same_date_twice():
    # rekap tgl 12 sudah jalan, loop kembali di 23:58:59.9 (masih sebelum jadwal)
    now = TZ.localize(datetime(2026, 11, 12, 23, 58, 59, 900000))
    assert next_recap_target(now, 23, 59, last_recap_date=date(2026, 11, 12)).date() == date(2026, 11, 13)


# ── schedule_recap loop: bangun telat lewat 00:00 ─────────

class _FakeDest:
    def __init__(self):
        self.sent = []

    async def send(self, msg, **kw):
        self.sent.append(msg)


class _FakeClient:
    def __init__(self, iterations):
        self._left = iterations
        self.dest  = _FakeDest()

    async def wait_until_ready(self):
        pass

    def is_closed(self):
        self._left -= 1
        return self._left < 0

    def get_channel(self, _id):
        return self.dest


@pytest.mark.parametrize("late_by", [
    timedelta(seconds=0),     # tepat waktu
    timedelta(seconds=61),    # bangun 00:00:01 hari berikutnya
    timedelta(minutes=2),     # bangun 00:01 hari berikutnya
])
def test_loop_recaps_target_date_even_if_woke_after_midnight(monkeypatch, late_by):
    clock = {"now": wib(2026, 11, 12, 23, 58, 0)}
    recapped = []

    async def fake_sleep(seconds):
        clock["now"] = clock["now"] + timedelta(seconds=seconds) + late_by

    async def fake_run_all_recaps(client, date_from, date_to):
        recapped.append((date_from, date_to, clock["now"]))
        return []

    monkeypatch.setattr(sched, "_now", lambda: clock["now"])
    monkeypatch.setattr(sched.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(sched, "run_all_recaps", fake_run_all_recaps)
    monkeypatch.setattr(sched, "RECAP_HOUR", 23)
    monkeypatch.setattr(sched, "RECAP_MINUTE", 59)

    asyncio.run(sched.schedule_recap(_FakeClient(iterations=2)))

    # iterasi 1 → tgl 12, iterasi 2 → tgl 13; tidak pernah dobel / loncat
    assert [r[0] for r in recapped] == [date(2026, 11, 12), date(2026, 11, 13)]
    assert all(r[0] == r[1] for r in recapped)
    if late_by >= timedelta(seconds=60):
        # buktikan memang bangun di hari berikutnya, tapi yang direkap tetap tgl 12
        assert recapped[0][2].date() == date(2026, 11, 13)
