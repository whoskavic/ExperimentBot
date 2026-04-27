import discord
from threading import Thread

from config import TOKEN
from services.scheduler import schedule_recap
from web.server import app, init_app


# ── Discord client ────────────────────────────────────────

intents = discord.Intents.default()
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


# ── Start ─────────────────────────────────────────────────

init_app(client)   # inject discord client ke Flask
keep_alive()
client.run(TOKEN)