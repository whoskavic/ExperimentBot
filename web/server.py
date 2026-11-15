import asyncio
import traceback
from datetime import date, datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_login import current_user

from config import TZ, DESTINATION_CHANNEL
from services.recap import run_all_recaps, send_recaps
from web.auth import init_auth
from web.cli import register_cli
from web.history import bp as history_bp

STATIC_DIR = Path(__file__).resolve().parent / "static"

# static_folder=None: asset disajikan lewat route `static_files` di bawah,
# supaya ikut dicek login (kecuali asset halaman login).
app = Flask(__name__, static_folder=None, template_folder="templates")
init_auth(app)
app.register_blueprint(history_bp)   # /api/sources, /api/recaps..., wajib login
register_cli(app)                    # list-sources, set-team, clear-team

# injected from bot.py at startup
_discord_client = None

def init_app(discord_client):
    global _discord_client
    _discord_client = discord_client


# ── routes ───────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route("/healthz")
def health():
    """Publik, tanpa login: cek bahwa web panel hidup."""
    return jsonify({"status": "ok"})


@app.route("/recap", methods=["POST"])
def trigger_recap():
    """
    Body JSON (optional):
    {
        "date_from": "2026-03-01",   ← opsional, default: hari ini
        "date_to":   "2026-03-09"    ← opsional, default: hari ini
    }
    Wajib login + header X-CSRFToken.
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

    triggered_by = f"panel:{current_user.username}"

    future = asyncio.run_coroutine_threadsafe(
        _do_recap(date_from, date_to, triggered_by),
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


async def _do_recap(date_from: date, date_to: date, triggered_by: str) -> dict:
    """
    Return: {"files": [nama file terkirim], "saved": save_report per source}
    save_report: [{source_id, source, kind, dates: [{recap_date, status, ...}]}]
    status: created | updated | unchanged | skipped | error
    """
    dest = _discord_client.get_channel(DESTINATION_CHANNEL)
    results, save_report = await run_all_recaps(_discord_client, date_from, date_to,
                                                triggered_by=triggered_by)

    if not results:
        return {"files": [], "saved": save_report}

    files = await send_recaps(dest, results, date_from, date_to)
    return {"files": files, "saved": save_report}
