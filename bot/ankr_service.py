"""Ankr Advanced API service — token balance queries."""

import json
import logging
from typing import Any

import requests

from bot.config import ANKR_ENDPOINT, MIN_BALANCE_USD

logger = logging.getLogger(__name__)


def _ankr_request(method: str, params: dict) -> dict:
    """Generic JSON-RPC call to Ankr multichain endpoint."""
    payload = {
        "id": 1,
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
    }
    headers = {"Content-Type": "application/json"}
    resp = requests.post(ANKR_ENDPOINT, headers=headers, data=json.dumps(payload), timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data and data["error"]:
        raise RuntimeError(f"Ankr API error: {data['error']}")
    return data.get("result", {})


def get_account_balance(
    wallet_address: str,
    blockchain: str | None = None,
) -> tuple[list[dict[str, Any]], float]:
    """
    Fetch token balances for a wallet using ankr_getAccountBalance.

    Args:
        wallet_address: The wallet address to query.
        blockchain: Specific chain to query, or None for all chains.

    Returns:
        (assets, total_balance_usd)
        assets: list of token dicts with balanceUsd > MIN_BALANCE_USD
        total_balance_usd: sum of all non-spam asset USD values
    """
    params: dict[str, Any] = {
        "walletAddress": wallet_address,
        "onlyWhitelisted": True,   # only CoinGecko-listed tokens
        "nativeFirst": True,
        "pageSize": 50,
        "pageToken": "",
    }
    if blockchain:
        params["blockchain"] = [blockchain]

    all_assets: list[dict] = []
    total_usd = 0.0

    while True:
        result = _ankr_request("ankr_getAccountBalance", params)
        assets = result.get("assets", [])

        for asset in assets:
            usd = float(asset.get("balanceUsd", "0") or "0")
            if usd >= MIN_BALANCE_USD:
                all_assets.append(asset)
                total_usd += usd

        next_token = result.get("nextPageToken")
        if not next_token:
            break
        params["pageToken"] = next_token

    # Sort by USD value descending
    all_assets.sort(key=lambda a: float(a.get("balanceUsd", "0") or "0"), reverse=True)

    logger.info(
        "Fetched balance for %s on %s: %d assets, $%.2f total",
        wallet_address, blockchain or "all chains", len(all_assets), total_usd,
    )
    return all_assets, total_usd
