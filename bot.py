import sys

import discord
from threading import Thread

from config import TOKEN, WEB_HOST, WEB_PORT
from services.scheduler import schedule_recap
from web.server import app, init_app


# ── Discord client ────────────────────────────────────────

intents = discord.Intents.default()
client = discord.Client(intents=intents)


# ── Flask thread ──────────────────────────────────────────

def run_web():
    app.run(host=WEB_HOST, port=WEB_PORT)

def keep_alive():
    t = Thread(target=run_web, daemon=True)
    t.start()


# ── Discord events ────────────────────────────────────────

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    client.loop.create_task(schedule_recap(client))


# ── Start ─────────────────────────────────────────────────

if not TOKEN:
    sys.exit("DISCORD_TOKEN belum diisi. Isi di .env (lihat .env.example).")

init_app(client)   # inject discord client ke Flask
keep_alive()
client.run(TOKEN)
