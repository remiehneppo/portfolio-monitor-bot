"""Configuration constants and helpers."""

import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

USERS_FILE = DATA_DIR / "users.json"

# ── Environment variables (loaded from .env or OS env) ─────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ANKR_API_KEY = os.getenv("ANKR_API_KEY", "")

# ── Ankr ───────────────────────────────────────────────────────────
ANKR_ENDPOINT = f"https://rpc.ankr.com/multichain/{ANKR_API_KEY}"

# ── Defaults ───────────────────────────────────────────────────────
DEFAULT_INTERVAL_HOURS = 6
MIN_INTERVAL_HOURS = 1
MAX_INTERVAL_HOURS = 168  # 1 week

# Minimum USD value for a token to be considered "not spam"
MIN_BALANCE_USD = 0.01

# Supported blockchains for display
SUPPORTED_CHAINS = [
    "arbitrum", "avalanche", "base", "bsc", "eth", "fantom",
    "flare", "gnosis", "linea", "optimism", "polygon", "scroll",
    "stellar", "story_mainnet", "syscoin", "taiko", "telos",
    "xai", "xlayer",
]
