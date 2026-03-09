import discord
import csv
import os
import pytz
from datetime import datetime, time
from discord.ext import tasks
from flask import Flask
from threading import Thread

TOKEN = os.environ["MTQ4MDQyOTA5OTYxMTcyMTc0OA.GotVqW.4UK-DOJAKOYPuphtPlHfv0phUDQDdqMqmMIxWo"]
CHANNEL_ID = int(os.environ["1480432967607259187"])

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ===== Flask server agar Render Web Service hidup =====
app = Flask('')

@app.route('/')
def home():
    return "Discord bot is running"

def run():
    app.run(host="0.0.0.0", port=10000)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ===== BOT READY =====
@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    recap_task.start()

# ===== TASK JAM 16:00 =====
@tasks.loop(minutes=1)
async def recap_task():

    tz = pytz.timezone("Asia/Jakarta")
    now = datetime.now(tz)

    if now.hour != 16 or now.minute != 0:
        return

    channel = client.get_channel(CHANNEL_ID)

    opening = {}
    closing = {}

    today = now.date()

    async for msg in channel.history(limit=1000):

        msg_time = msg.created_at.astimezone(tz)

        if msg_time.date() != today:
            continue

        lines = msg.content.splitlines()
        if not lines:
            continue

        header = lines[0].strip().upper()

        if header == "OPENING" and msg_time.time() < time(10,0):
            opening[msg.author.name] = 1

        if header == "CLOSING" and msg_time.time() > time(15,0):
            closing[msg.author.name] = 1

    users = set(opening.keys()) | set(closing.keys())

    filename = f"recap_{today}.csv"

    with open(filename,"w",newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date","user","opening","closing"])

        for u in users:
            writer.writerow([
                today,
                u.upper(),
                1 if opening.get(u) else 0,
                1 if closing.get(u) else 0
            ])

    await channel.send(
        f"Recap {today}",
        file=discord.File(filename)
    )

# ===== START =====
keep_alive()
client.run(TOKEN)
