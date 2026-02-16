#!/usr/bin/env python3
"""
Telegram Portfolio Monitor Bot — Entry point.

Usage:
    1. Copy .env.example to .env and fill in your tokens
    2. pip install -r requirements.txt
    3. python -m bot.main
"""

import logging
import sys

from dotenv import load_dotenv

# Load .env BEFORE importing config (which reads os.getenv)
load_dotenv()

from bot.config import ANKR_API_KEY, TELEGRAM_BOT_TOKEN  # noqa: E402
from bot.handlers import register_handlers, setup_user_jobs  # noqa: E402

from telegram.ext import Application  # noqa: E402

# ── Logging ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set. Create a .env file or set the env var.")
        sys.exit(1)
    if not ANKR_API_KEY:
        logger.error("ANKR_API_KEY not set. Create a .env file or set the env var.")
        sys.exit(1)

    logger.info("Starting Portfolio Monitor Bot...")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register command handlers
    register_handlers(app)

    # Set up scheduled jobs after bot startup
    async def _post_init(application: Application) -> None:
        await setup_user_jobs(application)

    app.post_init = _post_init

    # Start polling
    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
