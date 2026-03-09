import discord
import csv
from datetime import datetime, time
from discord.ext import tasks

TOKEN = "MTQ4MDQyOTA5OTYxMTcyMTc0OA.GotVqW.4UK-DOJAKOYPuphtPlHfv0phUDQDdqMqmMIxWo"
CHANNEL_ID = 1480432967607259187

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print("Bot ready")
    daily_recap.start()

@tasks.loop(minutes=1)
async def daily_recap():
    now = datetime.now()

    if now.hour != 16 or now.minute != 0:
        return

    channel = client.get_channel(CHANNEL_ID)

    opening = {}
    closing = {}

    target_date = now.date()

    async for msg in channel.history(limit=None):
        if msg.created_at.date() != target_date:
            continue

        lines = msg.content.splitlines()
        if not lines:
            continue

        header = lines[0].strip().upper()
        msg_time = msg.created_at.time()

        if header == "OPENING" and msg_time < time(10,0):
            opening[msg.author.name] = 1

        if header == "CLOSING" and msg_time > time(15,0):
            closing[msg.author.name] = 1

    users = set(opening.keys()) | set(closing.keys())

    file_name = f"recap_{target_date}.csv"

    with open(file_name, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date","user","opening","closing"])

        for u in users:
            writer.writerow([
                target_date,
                u.upper(),
                1 if opening.get(u) else 0,
                1 if closing.get(u) else 0
            ])

    await channel.send(
        f"Rekap {target_date}",
        file=discord.File(file_name)
    )

client.run(TOKEN)