"""Application configuration: paths, environment variables and defaults."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_PATH = Path(os.getenv("OTODOM_DB_PATH", BASE_DIR / "data" / "otodom.db"))
REPORTS_DIR = BASE_DIR / "reports"

# Listings are scored by the locally installed, logged-in Claude Code CLI (no API key).
# Override the executable when `claude` is not on the PATH of the process running the app.
CLAUDE_CLI = os.getenv("CLAUDE_CLI_PATH") or "claude"

# Claude models selectable in settings (model ID passed to `claude --model` -> UI label).
AI_MODELS = {
    "claude-sonnet-5-5": "Claude Sonnet 5.5 — dokładniejsza analiza",
    "claude-haiku-5-5": "Claude Haiku 5.5 — szybciej, mniej zużywa limit subskrypcji",
}
DEFAULT_AI_MODEL = "claude-sonnet-5-5"

SCAN_INTERVALS_MINUTES = [15, 30, 60, 120, 240, 720]

# Per-scan limits – protect against being blocked by Otodom and against runaway API costs.
MAX_SEARCH_PAGES = 2
MAX_NEW_LISTINGS_PER_SCAN = 30
MAX_EVALUATIONS_PER_SCAN = 30
MAX_AI_ATTEMPTS = 3
# Watched listings (favourites, contacted, high scores) drop off the newest-first search pages within
# days, so their detail pages are re-checked for price changes – a few per scan, each at most daily.
MAX_PRICE_RECHECKS_PER_SCAN = 10
PRICE_RECHECK_INTERVAL_HOURS = 24
REQUEST_DELAY_SECONDS = (1.5, 4.0)

CRITERION_WEIGHTS = ["Niska", "Średnia", "Wysoka", "Krytyczna"]

# Nominatim (OpenStreetMap) requires an identifying User-Agent and at most 1 request per second.
NOMINATIM_USER_AGENT = "otodom-analyzer/1.1 (personal flat search assistant)"

DEFAULT_SETTINGS: dict[str, Any] = {
    "filters": {
        "location": "mazowieckie/warszawa/warszawa/warszawa",
        "radius_km": 0,
        "price_min": None,
        "price_max": 900_000,
        "area_min": 40,
        "area_max": None,
        "rooms_min": 2,
        "rooms_max": 4,
        "price_per_m2_max": None,
        "market": "ALL",  # ALL | PRIMARY | SECONDARY
        "custom_url": "",
    },
    "criteria": [
        {"text": "Musi posiadać balkon lub taras", "weight": "Wysoka"},
        {"text": "Nie parter i nie ostatnie piętro bez windy", "weight": "Krytyczna"},
        {"text": "Stan: do zamieszkania lub do odświeżenia (brak dewastacji)", "weight": "Wysoka"},
        {"text": "Preferowane ciche otoczenie, z dala od głównych ulic", "weight": "Średnia"},
        {"text": "Dobra komunikacja (blisko metro/tramwaj)", "weight": "Średnia"},
    ],
    "ai_model": DEFAULT_AI_MODEL,
    "scan_interval_minutes": 60,
    "daily_token_limit": 100_000,  # 0 = no limit; one Claude Code evaluation uses ~10–15k tokens
    "notifications": {
        "enabled": False,
        "channel": "ntfy",  # ntfy | discord | telegram
        "webhook_url": "",  # ntfy topic URL or Discord webhook URL
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "min_score": 80,
        "price_drops": True,
    },
    "target": {
        "label": "Praca",
        "address": "",
        "mode": "transit",  # transit | car | bike | walk
        "latitude": None,
        "longitude": None,
        "resolved_address": "",
    },
}


def setting_or_env(saved_value: str | None, env_var: str) -> str | None:
    """A value saved in the app settings takes precedence over the environment (.env)."""
    return saved_value or os.getenv(env_var) or None


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx2 logs every request URL at INFO – Telegram URLs contain the bot token.
    logging.getLogger("httpx2").setLevel(logging.WARNING)
