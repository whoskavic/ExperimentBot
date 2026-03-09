import discord
import csv
import pytz
from datetime import datetime, time as dtime
from discord.ext import tasks
from flask import Flask
from threading import Thread

# ===== CONFIG =====
TOKEN = "MTQ4MDQyOTA5OTYxMTcyMTc0OA.GEcNvp.Ax6bxfhk9-0hXNa03MbNiUNPL9rKuayBSspgIk"
CHANNEL_ID = 1480432967607259187

# ===== DISCORD SETUP =====
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ===== TIMEZONE =====
tz = pytz.timezone("Asia/Jakarta")

# ===== STATE =====
last_run_date = None

# ===== Flask keep alive =====
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is alive"


def run_web():
    app.run(host="0.0.0.0", port=8080)


def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()


# ===== RECAP GENERATOR =====
async def generate_recap(channel):

    now = datetime.now(tz)
    today = now.date()

    opening = {}
    closing = {}

    start_of_day = tz.localize(datetime.combine(today, dtime.min))

    async for msg in channel.history(after=start_of_day):

        msg_time = msg.created_at.astimezone(tz)

        lines = msg.content.splitlines()
        if not lines:
            continue

        header = lines[0].strip().upper()
        user = msg.author.display_name

        # contoh header:
        # "SENIN, 09 MARET 2026 - OPENING"

        # OPENING jika <= 18:00 dan header mengandung OPENING
        if "OPENING" in header and msg_time.time() <= dtime(18, 0):
            opening[user] = 1

        # CLOSING jika >= 20:00 dan header mengandung CLOSING
        if "CLOSING" in header and msg_time.time() >= dtime(20, 0):
            closing[user] = 1

    users = sorted(set(opening.keys()) | set(closing.keys()))

    filename = f"recap_{today}.csv"

    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "user", "opening", "closing"])

        for u in users:
            writer.writerow([
                today,
                u.upper(), 1 if opening.get(u) else 0,
                1 if closing.get(u) else 0
            ])

    return filename, today


# ===== BOT READY =====
@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    recap_task.start()


# ===== COMMAND TEST =====
@client.event
async def on_message(message):

    if message.author == client.user:
        return

    if message.content.lower() == "!recap":

        await message.channel.send("Generating recap...")

        filename, today = await generate_recap(message.channel)

        await message.channel.send(f"Recap {today}",
                                   file=discord.File(filename))


# ===== AUTO RECAP 16:00 =====
@tasks.loop(minutes=1)
async def recap_task():

    global last_run_date

    now = datetime.now(tz)

    if now.hour != 16 or now.minute != 0:
        return

    if last_run_date == now.date():
        return

    last_run_date = now.date()

    channel = client.get_channel(CHANNEL_ID)

    if channel is None:
        print("Channel not found")
        return

    filename, today = await generate_recap(channel)

    await channel.send(f"Daily Recap {today}", file=discord.File(filename))


# ===== START =====
keep_alive()
client.run(TOKEN)
