import discord
from threading import Thread

from config import TOKEN, CHANNEL_IDS, THREAD_IDS
from services.recap import generate_recap
from services.scheduler import schedule_recap
from web.server import app, init_app
import os
from datetime import datetime
from config import TZ


# ── Discord client ────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


# ── Flask thread ──────────────────────────────────────────

def run_web():
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    t = Thread(target=run_web, daemon=True)
    t.start()


# ── Discord events ────────────────────────────────────────

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    client.loop.create_task(schedule_recap(client))


@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content.lower() == "!recap":
        current_id = message.channel.id

        if current_id not in CHANNEL_IDS and current_id not in THREAD_IDS:
            await message.channel.send("Command `!recap` tidak bisa dijalankan di sini.")
            return

        await message.channel.send("Generating recap...")

        today  = datetime.now(TZ).date()
        source = client.get_channel(current_id)
        filepath = await generate_recap(source, today, today)

        await message.channel.send(
            f"Recap `{today}` — **#{source.name}**",
            file=discord.File(filepath)
        )
        os.remove(filepath)


# ── Start ─────────────────────────────────────────────────

init_app(client)   # inject discord client ke Flask
keep_alive()
client.run(TOKEN)