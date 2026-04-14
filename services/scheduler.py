from datetime import datetime, date, timedelta

from config import TZ, DESTINATION_CHANNEL, RECAP_HOUR, RECAP_MINUTE
from services.recap import run_all_recaps, send_recaps


async def schedule_recap(client):
    import discord
    await client.wait_until_ready()

    while not client.is_closed():
        now    = datetime.now(TZ)
        target = now.replace(hour=RECAP_HOUR, minute=RECAP_MINUTE, second=0, microsecond=0)

        if now >= target:
            target += timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        print(f"Auto recap dijadwalkan: {target.strftime('%Y-%m-%d %H:%M:%S')} WIB")

        import asyncio
        await asyncio.sleep(wait_seconds)

        dest = client.get_channel(DESTINATION_CHANNEL)
        if dest is None:
            print("Destination channel not found")
            continue

        today   = datetime.now(TZ).date()
        results = await run_all_recaps(client, today, today)

        if not results:
            await dest.send("Tidak ada data recap hari ini.")
            continue

        await send_recaps(dest, results, today, today)