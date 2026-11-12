import asyncio
from datetime import datetime, date, time as dtime, timedelta

from config import TZ, DESTINATION_CHANNEL, RECAP_HOUR, RECAP_MINUTE
from services.recap import run_all_recaps, send_recaps_scheduled


def _now() -> datetime:
    """Waktu sekarang di WIB. Dipisah supaya bisa di-mock di test."""
    return datetime.now(TZ)


def next_recap_target(now: datetime, hour: int, minute: int,
                      last_recap_date: date | None = None) -> datetime:
    """
    Hitung waktu rekap berikutnya (aware, WIB) dari `now`.

    - `now` boleh dalam zona apa pun; dikonversi ke WIB dulu.
    - Kalau jam target hari ini sudah lewat → besok.
    - Kalau tanggal target sama dengan rekap terakhir (mis. bangun sedikit
      lebih awal dari jadwal) → besok, supaya tidak dobel.

    Tanggal rekap = `target.date()`, BUKAN tanggal saat bangun dari sleep.
    """
    now_wib = now.astimezone(TZ)
    target  = TZ.localize(datetime.combine(now_wib.date(), dtime(hour, minute)))

    if now_wib >= target or (last_recap_date is not None and target.date() <= last_recap_date):
        target = TZ.localize(datetime.combine(target.date() + timedelta(days=1), dtime(hour, minute)))

    return target


async def schedule_recap(client):
    await client.wait_until_ready()

    last_recap_date: date | None = None

    while not client.is_closed():
        now    = _now()
        target = next_recap_target(now, RECAP_HOUR, RECAP_MINUTE, last_recap_date)

        # Tanggal rekap dikunci SEBELUM sleep. Kalau bot bangun lewat 00:00
        # (sleep molor / reconnect), yang direkap tetap tanggal target.
        recap_date = target.date()

        wait_seconds = (target - now).total_seconds()
        print(f"Auto recap dijadwalkan: {target.strftime('%Y-%m-%d %H:%M:%S')} WIB")

        await asyncio.sleep(wait_seconds)
        last_recap_date = recap_date

        dest = client.get_channel(DESTINATION_CHANNEL)
        if dest is None:
            print("Destination channel not found")
            continue

        results = await run_all_recaps(client, recap_date, recap_date)

        if not results:
            await dest.send("Tidak ada data recap hari ini.")
            continue

        await send_recaps_scheduled(dest, results, recap_date, recap_date)
