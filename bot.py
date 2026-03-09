import discord
import csv
import os
import pytz
import asyncio
import io
from datetime import datetime, time as dtime
from discord.ext import tasks
from flask import Flask
from threading import Thread

TOKEN = os.environ["DISCORD_TOKEN"]
CHANNEL_ID = int(os.environ["CHANNEL_ID"])
print(f"TOKEN loaded: {TOKEN[:10]}...")
print(f"CHANNEL_ID loaded: {CHANNEL_ID}")

# ===== DISCORD SETUP =====
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ===== TIMEZONE =====
tz = pytz.timezone("Asia/Jakarta")

# ===== RUN STATE =====
last_run_date = None

# ===== Flask server agar Render tidak sleep =====
app = Flask('')

@app.route('/')
def home():
    return "Discord bot is running"

def run():
    for port in [10000, 10001, 10002]:
        try:
            print(f"Trying Flask on port {port}...")
            app.run(host="0.0.0.0", port=port)
            break
        except OSError:
            print(f"Port {port} busy, trying next...")

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()

# ===== RECAP FUNCTION =====
async def generate_recap(channel):
    now = datetime.now(tz)
    today = now.date()
    opening = {}
    closing = {}
    start_of_day = tz.localize(datetime.combine(today, dtime.min))

    async for msg in channel.history(after=start_of_day, limit=500):
        msg_time = msg.created_at.astimezone(tz)
        if msg_time.date() != today:
            continue
        lines = msg.content.splitlines()
        if not lines:
            continue
        header = lines[0].strip().upper()
        user = msg.author.display_name
        if header == "OPENING" and msg_time.time() < dtime(10, 0):
            opening[user] = 1
        if header == "CLOSING" and msg_time.time() > dtime(15, 0):
            closing[user] = 1

    users = sorted(set(opening.keys()) | set(closing.keys()))

    # Tulis CSV langsung ke memory, tidak menyentuh disk sama sekali
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "user", "opening", "closing"])
    for u in users:
        writer.writerow([
            today,
            u.upper(),
            1 if opening.get(u) else 0,
            1 if closing.get(u) else 0
        ])

    buffer.seek(0)
    discord_file = discord.File(fp=io.BytesIO(buffer.getvalue().encode()), filename=f"recap_{today}.csv")
    return discord_file, today

# ===== BOT READY =====
@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    if not recap_task.is_running():
        recap_task.start()

# ===== TEST COMMAND =====
@client.event
async def on_message(message):
    if message.author == client.user:
        return
    if message.content.lower() == "!recap":
        await message.channel.send("Generating recap...")
        discord_file, today = await generate_recap(message.channel)
        await message.channel.send(f"Recap {today}", file=discord_file)

# ===== AUTO TASK JAM 16:00 =====
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
    print("Generating automatic recap...")
    discord_file, today = await generate_recap(channel)
    await channel.send(f"Daily Recap {today}", file=discord_file)

# ===== START BOT WITH RETRY =====
async def start_bot():
    print("start_bot() called")
    max_retries = 5
    for attempt in range(max_retries):
        try:
            print(f"Attempting to connect (attempt {attempt + 1}/{max_retries})...")
            await client.start(TOKEN)
            print("client.start() returned normally")
            break
        except discord.errors.HTTPException as e:
            print(f"HTTPException: status={e.status}, code={e.code}, text={e.text}")
            if e.status == 429:
                wait_time = (2 ** attempt) * 15
                print(f"Rate limited! Waiting {wait_time}s before retry...")
                await asyncio.sleep(wait_time)
            else:
                print(f"Non-429 HTTP error, stopping.")
                raise
        except discord.errors.LoginFailure as e:
            print(f"LoginFailure: {e}")
            break
        except SystemExit as e:
            print(f"SystemExit caught: {e}")
            break
        except BaseException as e:   # <-- tangkap SEMUA termasuk KeyboardInterrupt
            import traceback
            print(f"BaseException: {type(e).__name__}: {e}")
            traceback.print_exc()
            break

    print("start_bot() loop ended")

# ===== TEST KONEKSI KE DISCORD =====
import aiohttp

async def test_discord_connection():
    print("Testing connection to Discord API...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://discord.com/api/v10/gateway") as resp:
                print(f"Discord API response: {resp.status}")
                text = await resp.text()
                print(f"Response body: {text[:200]}")
    except Exception as e:
        print(f"Connection test failed: {type(e).__name__}: {e}")

asyncio.run(test_discord_connection())

# ===== START =====
print("=== BOT STARTING ===")
try:
    keep_alive()
    print("=== KEEP ALIVE STARTED ===")
except Exception as e:
    import traceback
    print(f"keep_alive error: {type(e).__name__}: {e}")
    traceback.print_exc()

try:
    asyncio.run(start_bot())
except KeyboardInterrupt:
    print("Interrupted")
except Exception as e:
    import traceback
    print(f"Top-level error: {type(e).__name__}: {e}")
    traceback.print_exc()
finally:
    print("=== PROCESS ENDING ===")
