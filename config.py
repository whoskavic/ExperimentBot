import json
import os
from pathlib import Path

import pytz
from dotenv import load_dotenv

# ── paths ─────────────────────────────────────────────────
# Semua path relatif diresolusi terhadap folder proyek, bukan CWD.
BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")


def _load_json(name: str) -> dict:
    path = BASE_DIR / name
    if not path.exists():
        print(f"[config] {name} tidak ditemukan — salin dari {Path(name).stem}.example.json")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _env_positive_int(name: str) -> int | None:
    """Kosong → None. Selain bilangan bulat > 0 → error yang jelas saat start."""
    raw = (os.getenv(name) or "").strip()
    if raw == "":
        return None
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"[config] {name} harus bilangan bulat > 0 (atau kosong), bukan {raw!r}")
    if value <= 0:
        raise SystemExit(f"[config] {name} harus bilangan bulat > 0 (atau kosong), bukan {raw!r}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


_config  = _load_json("appsettings.json")
USER_MAP = _load_json("user.json")

# ── secrets (.env, fallback appsettings.json sementara) ────
TOKEN      = os.getenv("DISCORD_TOKEN") or _config.get("bot_token") or ""
SECRET_KEY = os.getenv("SECRET_KEY") or ""

# ── discord ───────────────────────────────────────────────
CHANNEL_IDS          = _config.get("channel_ids", [])
THREAD_IDS           = _config.get("thread_ids", [])
DESTINATION_CHANNEL  = _config.get("destination_channel", 0)
RECAP_HOUR           = _config.get("recap_hour", 23)
RECAP_MINUTE         = _config.get("recap_minute", 59)

# ── web ───────────────────────────────────────────────────
WEB_HOST              = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT              = int(os.getenv("WEB_PORT", "8080"))
SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", False)
LOGIN_RATE_LIMIT      = os.getenv("LOGIN_RATE_LIMIT", "5 per minute")   # hanya percobaan gagal
SESSION_LIFETIME_MINUTES = _env_positive_int("SESSION_LIFETIME_MINUTES")  # None = sampai browser ditutup

# ── database ──────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///radsoftbot.db")

TZ = pytz.timezone("Asia/Jakarta")
