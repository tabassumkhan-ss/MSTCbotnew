import os
import sys
import logging
import asyncio

from dotenv import load_dotenv
from telegram import Bot
from telegram import WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup

# local helpers
sys.path.append(os.path.dirname(__file__))

# -------------------------
# Env & logging
# -------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)

# -------------------------
# Helper: safe async send
# -------------------------
def send_message_safe(**kwargs):
    """
    Safely send Telegram messages from sync Flask context
    """
    try:
        asyncio.run(bot.send_message(**kwargs))
    except RuntimeError:
        # If event loop already running
        loop = asyncio.get_event_loop()
        loop.create_task(bot.send_message(**kwargs))

# -------------------------
# Command handler
# -------------------------
def handle_command(update: dict):
    """
    Handles Telegram webhook updates
    """
    try:
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return

        chat_id = msg["chat"]["id"]
        from_user = msg.get("from", {})
        user_id = from_user.get("id")
        text = (msg.get("text") or "").strip()

        logger.info("Received from %s: %s", user_id, text)

        # Ignore non-commands
        if not text.startswith("/"):
            return

        cmd = text.split()[0].lower()

        # -------------------------
        # /start
        # -------------------------
        if cmd == "/start":
            text_to_send = "Welcome! Tap below to open the deposit mini app."

            webapp_url = (
                "https://mstcbotnew-production.up.railway.app/"
                "static/telegram_mini_app.html"
            )

            keyboard = [[
                InlineKeyboardButton(
                    text="Open Deposit Mini App",
                    web_app=WebAppInfo(url=webapp_url)
                )
            ]]

            send_message_safe(
                chat_id=chat_id,
                text=text_to_send,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        # -------------------------
        # /balance (placeholder)
        # -------------------------
        if cmd == "/balance":
            send_message_safe(
                chat_id=chat_id,
                text="Please open the mini app to view your balance."
            )
            return

        # -------------------------
        # Unknown command
        # -------------------------
        send_message_safe(
            chat_id=chat_id,
            text="Unknown command. Please use /start."
        )

    except Exception:
        logger.exception("Error handling Telegram update")
