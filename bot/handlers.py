"""Telegram bot command handlers and scheduler."""

import logging
from datetime import datetime, timezone
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from bot import ankr_service, charts, storage
from bot.config import (
    DEFAULT_INTERVAL_HOURS,
    MAX_INTERVAL_HOURS,
    MIN_INTERVAL_HOURS,
)

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────

def _short_addr(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}" if len(addr) > 12 else addr


async def _fetch_and_save(user_id: str, wallet: dict, timestamp: str | None = None) -> str:
    """Fetch balance for one wallet (multichain) and save snapshot. Returns summary text."""
    address = wallet["address"]
    try:
        assets, total_usd = ankr_service.get_account_balance(address)
        storage.save_portfolio_snapshot(user_id, address, assets, total_usd, timestamp=timestamp)

        lines = [f"*{_short_addr(address)}* (multichain) — ${total_usd:,.2f}"]
        for a in assets[:10]:
            sym = a.get("tokenSymbol", "?")
            bal = a.get("balance", "0")
            usd = float(a.get("balanceUsd", "0") or "0")
            chain = a.get("blockchain", "?")
            lines.append(f"  `{sym}` ({chain}): {float(bal):,.6g} ≈ ${usd:,.2f}")
        if len(assets) > 10:
            lines.append(f"  _...and {len(assets) - 10} more tokens_")
        return "\n".join(lines)
    except Exception as e:
        logger.exception("Error fetching balance for %s", address)
        return f"*{_short_addr(address)}* — Error: {e}"


# ── /start ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    storage.ensure_user(user_id)
    await update.message.reply_text(
        "👛 *Portfolio Monitor Bot*\n\n"
        "Commands:\n"
        "/add\\_wallet `<address>` — Add wallet (multichain)\n"
        "/remove\\_wallet `<address>` — Remove wallet\n"
        "/list\\_wallets — List tracked wallets\n"
        "/set\\_interval `<hours>` — Set check interval (default 6h)\n"
        "/refresh — Fetch portfolio now\n"
        "/portfolio `[address]` — Show latest balances\n"
        "/chart `[address]` — Portfolio history chart\n"
        "/breakdown `[address]` — Token breakdown chart\n\n"
        "Balances are checked across all supported chains automatically.",
        parse_mode="Markdown",
    )


# ── /add_wallet ────────────────────────────────────────────────────

async def cmd_add_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    args = context.args or []

    if len(args) < 1:
        await update.message.reply_text(
            "Usage: /add\\_wallet `<address>`\n"
            "Balances will be checked across all chains.",
            parse_mode="Markdown",
        )
        return

    address = args[0].lower()

    if not address.startswith("0x") or len(address) != 42:
        await update.message.reply_text("Invalid address. Must be 0x... (42 chars).")
        return

    added = storage.add_wallet(user_id, address)
    if not added:
        await update.message.reply_text("This wallet is already being tracked.")
        return

    await update.message.reply_text(
        f"✅ Added `{_short_addr(address)}` (multichain)\n"
        "Fetching initial data...",
        parse_mode="Markdown",
    )

    wallet = {"address": address}
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    summary = await _fetch_and_save(user_id, wallet, timestamp=ts)
    await update.message.reply_text(summary, parse_mode="Markdown")

    # Schedule periodic checks for this user
    reschedule_user_job(context.application, user_id)


# ── /remove_wallet ─────────────────────────────────────────────────

async def cmd_remove_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    args = context.args or []

    if len(args) < 1:
        await update.message.reply_text(
            "Usage: /remove\\_wallet `<address>`",
            parse_mode="Markdown",
        )
        return

    address = args[0].lower()

    removed = storage.remove_wallet(user_id, address)
    if removed:
        await update.message.reply_text(f"🗑 Removed `{_short_addr(address)}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("Wallet not found in your list.")


# ── /list_wallets ──────────────────────────────────────────────────

async def cmd_list_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    wallets = storage.get_wallets(user_id)
    interval = storage.get_interval(user_id)

    if not wallets:
        await update.message.reply_text("No wallets tracked. Use /add\\_wallet to start.", parse_mode="Markdown")
        return

    lines = [f"📋 *Your Wallets* (check every {interval}h, multichain):\n"]
    for i, w in enumerate(wallets, 1):
        lines.append(f"{i}. `{w['address']}`")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /set_interval ──────────────────────────────────────────────────

async def cmd_set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    args = context.args or []

    if not args:
        current = storage.get_interval(user_id)
        await update.message.reply_text(
            f"Current interval: *{current}h*\n"
            f"Usage: /set\\_interval `<hours>` ({MIN_INTERVAL_HOURS}-{MAX_INTERVAL_HOURS})",
            parse_mode="Markdown",
        )
        return

    try:
        hours = int(args[0])
    except ValueError:
        await update.message.reply_text("Please provide a number (hours).")
        return

    if not (MIN_INTERVAL_HOURS <= hours <= MAX_INTERVAL_HOURS):
        await update.message.reply_text(
            f"Interval must be between {MIN_INTERVAL_HOURS} and {MAX_INTERVAL_HOURS} hours."
        )
        return

    storage.set_interval(user_id, hours)
    await update.message.reply_text(f"⏰ Check interval set to *{hours}h*", parse_mode="Markdown")

    # Reschedule with new interval
    reschedule_user_job(context.application, user_id)


# ── /refresh ───────────────────────────────────────────────────────

async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    wallets = storage.get_wallets(user_id)

    if not wallets:
        await update.message.reply_text("No wallets tracked. Use /add\\_wallet first.", parse_mode="Markdown")
        return

    await update.message.reply_text(f"🔄 Refreshing {len(wallets)} wallet(s)...")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    summaries = []
    for w in wallets:
        s = await _fetch_and_save(user_id, w, timestamp=ts)
        summaries.append(s)

    msg = "\n\n".join(summaries)
    # Telegram has 4096 char limit
    if len(msg) > 4000:
        msg = msg[:4000] + "\n\n_...truncated_"
    await update.message.reply_text(msg, parse_mode="Markdown")


# ── /portfolio ─────────────────────────────────────────────────────

async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    args = context.args or []
    wallet_filter: Optional[str] = args[0].lower() if args else None

    rows = storage.load_portfolio_history(user_id)
    if not rows:
        await update.message.reply_text("No portfolio data. Use /refresh to fetch.")
        return

    # Get latest timestamp
    latest_ts = max(r["timestamp"] for r in rows)
    latest = [r for r in rows if r["timestamp"] == latest_ts]

    if wallet_filter:
        latest = [r for r in latest if r["wallet_address"].lower() == wallet_filter]

    if not latest:
        await update.message.reply_text("No data found for that wallet.")
        return

    # Group by wallet
    from collections import defaultdict
    by_wallet: dict[str, list] = defaultdict(list)
    for r in latest:
        by_wallet[r["wallet_address"]].append(r)

    lines = [f"📊 *Portfolio* ({latest_ts})\n"]
    grand_total = 0.0
    for addr, tokens in by_wallet.items():
        wallet_total = sum(float(t.get("balance_usd", 0) or 0) for t in tokens)
        grand_total += wallet_total
        chain_info = tokens[0].get("blockchain", "")
        lines.append(f"*{_short_addr(addr)}* ({chain_info}) — ${wallet_total:,.2f}")
        for t in sorted(tokens, key=lambda x: float(x.get("balance_usd", 0) or 0), reverse=True)[:10]:
            sym = t["token_symbol"]
            bal = float(t.get("balance", 0) or 0)
            usd = float(t.get("balance_usd", 0) or 0)
            lines.append(f"  `{sym}`: {bal:,.6g} ≈ ${usd:,.2f}")
        if len(tokens) > 10:
            lines.append(f"  _...+{len(tokens) - 10} more_")
        lines.append("")

    lines.append(f"💰 *Grand Total: ${grand_total:,.2f}*")

    msg = "\n".join(lines)
    if len(msg) > 4000:
        msg = msg[:4000] + "\n\n_...truncated_"
    await update.message.reply_text(msg, parse_mode="Markdown")


# ── /chart ─────────────────────────────────────────────────────────

async def cmd_chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    args = context.args or []
    wallet_filter: Optional[str] = args[0].lower() if args else None

    rows = storage.load_portfolio_history(user_id)
    if not rows:
        await update.message.reply_text("No portfolio data. Use /refresh to fetch.")
        return

    buf = charts.generate_wallet_chart(rows, wallet_address=wallet_filter)
    if buf is None:
        await update.message.reply_text("No chart data available for this wallet.")
        return

    await update.message.reply_photo(photo=buf, caption="📈 Portfolio History")


# ── /breakdown ─────────────────────────────────────────────────────

async def cmd_breakdown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    args = context.args or []
    wallet_filter: Optional[str] = args[0].lower() if args else None

    rows = storage.load_portfolio_history(user_id)
    if not rows:
        await update.message.reply_text("No portfolio data. Use /refresh to fetch.")
        return

    buf = charts.generate_token_breakdown_chart(rows, wallet_address=wallet_filter)
    if buf is None:
        await update.message.reply_text("No breakdown data available.")
        return

    await update.message.reply_photo(photo=buf, caption="📊 Token Breakdown (Quantity & USD)")


# ── Scheduler callback ────────────────────────────────────────────

async def scheduled_check_user(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called periodically per-user to check their wallet portfolios."""
    user_id = context.job.data.get("user_id") if context.job.data else None
    if not user_id:
        return

    all_users = storage.get_all_users()
    user_data = all_users.get(user_id)
    if not user_data:
        return

    wallets = user_data.get("wallets", [])
    if not wallets:
        return

    logger.info("Running scheduled check for user %s (%d wallets)", user_id, len(wallets))

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    summaries = []
    for w in wallets:
        try:
            address = w["address"]
            assets, total_usd = ankr_service.get_account_balance(address)
            storage.save_portfolio_snapshot(user_id, address, assets, total_usd, timestamp=ts)
            summaries.append(
                f"*{_short_addr(address)}*: "
                f"{len(assets)} tokens, ${total_usd:,.2f}"
            )
        except Exception as e:
            logger.exception("Scheduled check failed for %s/%s", user_id, w["address"])
            summaries.append(f"*{_short_addr(w['address'])}*: Error — {e}")

    # Notify user
    try:
        msg = "🔔 *Scheduled Portfolio Update*\n\n" + "\n".join(summaries)
        if len(msg) > 4000:
            msg = msg[:4000] + "\n\n_...truncated_"
        await context.bot.send_message(chat_id=int(user_id), text=msg, parse_mode="Markdown")
    except Exception as e:
        logger.error("Failed to send message to user %s: %s", user_id, e)


# ── Per-user interval scheduler ───────────────────────────────────

def reschedule_user_job(app: Application, user_id: str) -> None:
    """(Re)schedule a recurring job for a specific user."""
    job_queue = app.job_queue
    job_name = f"check_{user_id}"

    # Remove existing jobs for this user
    existing = job_queue.get_jobs_by_name(job_name)
    for job in existing:
        job.schedule_removal()

    user_data = storage.get_all_users().get(user_id, {})
    wallets = user_data.get("wallets", [])
    if not wallets:
        return

    interval_hours = user_data.get("interval_hours", DEFAULT_INTERVAL_HOURS)
    job_queue.run_repeating(
        scheduled_check_user,
        interval=interval_hours * 3600,
        first=interval_hours * 3600,
        name=job_name,
        data={"user_id": user_id},
    )
    logger.info("Scheduled job for user %s every %dh", user_id, interval_hours)


async def setup_user_jobs(app: Application) -> None:
    """Set up per-user recurring jobs based on their interval settings."""
    all_users = storage.get_all_users()
    for user_id, user_data in all_users.items():
        wallets = user_data.get("wallets", [])
        if not wallets:
            continue
        reschedule_user_job(app, user_id)


# ── Register handlers ─────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    """Register all command handlers with the Application."""
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("add_wallet", cmd_add_wallet))
    app.add_handler(CommandHandler("remove_wallet", cmd_remove_wallet))
    app.add_handler(CommandHandler("list_wallets", cmd_list_wallets))
    app.add_handler(CommandHandler("set_interval", cmd_set_interval))
    app.add_handler(CommandHandler("refresh", cmd_refresh))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("chart", cmd_chart))
    app.add_handler(CommandHandler("breakdown", cmd_breakdown))
