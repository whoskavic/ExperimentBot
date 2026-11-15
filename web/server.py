import asyncio
import traceback
from datetime import date, datetime

from flask import Flask, jsonify, request, send_from_directory

from config import TZ, DESTINATION_CHANNEL
from services.recap import run_all_recaps, send_recaps

app = Flask(__name__, static_folder="static", static_url_path="")

# injected from bot.py at startup
_discord_client = None

def init_app(discord_client):
    global _discord_client
    _discord_client = discord_client


# ── routes ───────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/recap", methods=["POST"])
def trigger_recap():
    """
    Body JSON (optional):
    {
        "date_from": "2026-03-01",   ← opsional, default: hari ini
        "date_to":   "2026-03-09"    ← opsional, default: hari ini
    }
    """
    body      = request.get_json(silent=True) or {}
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")

    try:
        date_from = date.fromisoformat(body.get("date_from", today_str))
        date_to   = date.fromisoformat(body.get("date_to",   today_str))
    except ValueError as e:
        return jsonify({"status": "error", "message": f"Format tanggal salah: {e}"}), 400

    if date_from > date_to:
        return jsonify({"status": "error", "message": "date_from tidak boleh lebih besar dari date_to"}), 400

    future = asyncio.run_coroutine_threadsafe(
        _do_recap(date_from, date_to),
        _discord_client.loop
    )

    try:
        out = future.result(timeout=120)
        return jsonify({"status": "ok", "date_from": str(date_from), "date_to": str(date_to),
                        "files": out["files"], "saved": out["saved"]})
    except Exception as e:
        traceback.print_exc()
        msg = str(e) or type(e).__name__
        return jsonify({"status": "error", "message": msg}), 500


async def _do_recap(date_from: date, date_to: date) -> dict:
    """
    Return: {"files": [nama file terkirim], "saved": save_report per source}
    save_report: [{source_id, source, kind, dates: [{recap_date, status, ...}]}]
    status: created | updated | unchanged | skipped | error
    """
    dest = _discord_client.get_channel(DESTINATION_CHANNEL)
    # TODO Langkah 6: "panel:<username>" setelah ada login
    results, save_report = await run_all_recaps(_discord_client, date_from, date_to,
                                                triggered_by="panel")

    if not results:
        return {"files": [], "saved": save_report}

    files = await send_recaps(dest, results, date_from, date_to)
    return {"files": files, "saved": save_report}