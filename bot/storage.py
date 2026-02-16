"""Persistent user data (JSON) and portfolio CSV storage."""

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.config import DATA_DIR, DEFAULT_INTERVAL_HOURS, USERS_FILE

logger = logging.getLogger(__name__)


# ── User data shape ────────────────────────────────────────────────
# {
#   "<telegram_user_id>": {
#     "wallets": [
#       {"address": "0x...", "blockchain": "eth"},
#       ...
#     ],
#     "interval_hours": 6
#   }
# }


def _load_users() -> dict[str, Any]:
    if USERS_FILE.exists():
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_users(data: dict[str, Any]) -> None:
    with open(USERS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def ensure_user(user_id: str) -> dict:
    """Return user entry, creating it if needed."""
    users = _load_users()
    if user_id not in users:
        users[user_id] = {
            "wallets": [],
            "interval_hours": DEFAULT_INTERVAL_HOURS,
        }
        _save_users(users)
    return users[user_id]


def add_wallet(user_id: str, address: str) -> bool:
    """Add a wallet. Returns True if newly added, False if duplicate."""
    users = _load_users()
    ensure_user(user_id)  # side-effect: creates entry if missing
    users = _load_users()  # reload after ensure

    address_lower = address.lower()
    for w in users[user_id]["wallets"]:
        if w["address"] == address_lower:
            return False

    users[user_id]["wallets"].append({"address": address_lower})
    _save_users(users)
    return True


def remove_wallet(user_id: str, address: str) -> bool:
    """Remove a wallet. Returns True if removed."""
    users = _load_users()
    if user_id not in users:
        return False

    address_lower = address.lower()
    original = users[user_id]["wallets"]
    users[user_id]["wallets"] = [
        w for w in original
        if w["address"] != address_lower
    ]
    if len(users[user_id]["wallets"]) == len(original):
        return False
    _save_users(users)
    return True


def get_wallets(user_id: str) -> list[dict]:
    user = ensure_user(user_id)
    return user["wallets"]


def set_interval(user_id: str, hours: int) -> None:
    users = _load_users()
    ensure_user(user_id)
    users = _load_users()
    users[user_id]["interval_hours"] = hours
    _save_users(users)


def get_interval(user_id: str) -> int:
    user = ensure_user(user_id)
    return user.get("interval_hours", DEFAULT_INTERVAL_HOURS)


def get_all_users() -> dict[str, Any]:
    return _load_users()


# ── Portfolio CSV ──────────────────────────────────────────────────
# File per user: data/portfolio_<user_id>.csv
# Columns: timestamp, wallet_address, blockchain, token_symbol, token_name,
#           balance, token_price_usd, balance_usd, total_wallet_usd


def _portfolio_path(user_id: str) -> Path:
    return DATA_DIR / f"portfolio_{user_id}.csv"


CSV_COLUMNS = [
    "timestamp",
    "wallet_address",
    "blockchain",
    "token_symbol",
    "token_name",
    "contract_address",
    "balance",
    "token_price_usd",
    "balance_usd",
    "total_wallet_usd",
]


def save_portfolio_snapshot(
    user_id: str,
    wallet_address: str,
    assets: list[dict],
    total_usd: float,
    timestamp: str | None = None,
) -> Path:
    """Append a portfolio snapshot to the user's CSV file.

    Args:
        timestamp: Shared timestamp string for a batch of wallets.
                   If None, generates current UTC time.
    """
    path = _portfolio_path(user_id)
    file_exists = path.exists()

    now = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()

        for asset in assets:
            writer.writerow({
                "timestamp": now,
                "wallet_address": wallet_address,
                "blockchain": asset.get("blockchain", ""),
                "token_symbol": asset.get("tokenSymbol", ""),
                "token_name": asset.get("tokenName", ""),
                "contract_address": asset.get("contractAddress", "native"),
                "balance": asset.get("balance", "0"),
                "token_price_usd": asset.get("tokenPrice", "0"),
                "balance_usd": asset.get("balanceUsd", "0"),
                "total_wallet_usd": f"{total_usd:.2f}",
            })

    logger.info("Saved snapshot for user %s, wallet %s (%d assets)", user_id, wallet_address, len(assets))
    return path


def load_portfolio_history(user_id: str) -> list[dict]:
    """Load all rows from the user's portfolio CSV."""
    path = _portfolio_path(user_id)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))
