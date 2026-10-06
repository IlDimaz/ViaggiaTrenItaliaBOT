"""Central configuration, loaded from environment / .env file."""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# How far ahead the departures board looks (ViaggiaTreno itself covers
# ~15 min before to 90 min after the requested time; we page forward).
DEPARTURES_WINDOW_MINUTES: int = int(os.getenv("DEPARTURES_WINDOW_MINUTES", "90"))

# Background infomobilita polling interval (seconds).
ALERTS_POLL_SECONDS: int = int(os.getenv("ALERTS_POLL_SECONDS", "300"))

# Interval for live train-tracking updates (seconds).
TRACK_SECONDS: int = int(os.getenv("TRACK_SECONDS", "300"))

# How many minutes before a scheduled train departure the bot starts reporting
# its status on the configured weekdays.
SCHEDULE_LEAD_MINUTES: int = int(os.getenv("SCHEDULE_LEAD_MINUTES", "30"))

# SQLite database used for per-chat settings.
DB_PATH = BASE_DIR / "trenitalia_bot.sqlite3"

# Italian railway timezone.
ROME = ZoneInfo("Europe/Rome")

# HTTP behaviour.
HTTP_TIMEOUT = 20.0

# ViaggiaTreno/lefrecce are fronted by Akamai, which answers 403 to clients with
# an obviously automated User-Agent. A realistic browser UA (plus the matching
# Accept headers) keeps the free endpoints reachable. Override via USER_AGENT.
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
).strip()

# Shared headers for every Trenitalia HTTP request, centralised so the two
# clients cannot drift apart.
HTTP_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.5",
}

# Telegram user id(s) allowed to run admin-only commands (/stats). Comma- or
# space-separated. Leave empty to disable admin commands.
_admin_raw = os.getenv("ADMIN_USER_IDS", "") or os.getenv("ADMIN_USER_ID", "")
ADMIN_USER_IDS: set[int] = {
    int(part)
    for part in _admin_raw.replace(",", " ").split()
    if part.lstrip("-").isdigit()
}


def require_token() -> str:
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and "
            "fill in your bot token from @BotFather."
        )
    return TELEGRAM_BOT_TOKEN
