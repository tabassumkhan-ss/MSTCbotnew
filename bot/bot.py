import os
import sys
import logging

from dotenv import load_dotenv
from telegram import Bot
from telegram import WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from utils import is_admin, call_backend

# local helpers
sys.path.append(os.path.dirname(__file__))


load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)


def handle_command(update):
    """
    update is a dict from getUpdates 'message' object (simplified).
    """
    try:
        msg = update.get('message') or update.get('edited_message')
        if not msg:
            return

        chat_id = msg['chat']['id']
        from_user = msg['from']
        user_id = from_user.get('id')
        text = msg.get('text', '').strip()
        logger.info("Received from %s: %s", user_id, text)

        if not text.startswith('/'):
            # ignore non-command messages
            return

        parts = text.split()
        cmd = parts[0].lower()
        args = parts[1:]

        # built-in commands
                # built-in commands
        if cmd == '/start':
            # Parse optional referral code from deep link (/start 12345)
            ref_code = args[0] if args else None

            from_user = msg.get('from', {}) or {}
            tg_id = from_user.get('id')
            username = from_user.get('username')
            first_name = from_user.get('first_name')

            # Default values in case backend call fails
            text_to_send = "Welcome! Tap below to open the deposit mini app."
            webapp_url = "https://mstcbotnew-production.up.railway.app/webapp"
            button_label = "Open Deposit Mini App"

            # Ask backend about this user (register + activation status + referral)
            try:
                payload = {
                    "telegram_id": tg_id,
                    "username": username,
                    "first_name": first_name,
                    "ref_code": ref_code,
                }
                r = call_backend('/bot/start', method='POST', json=payload)
                if r is not None and r.ok:
                    data = r.json()
                    text_to_send = data.get("message", text_to_send)
                    webapp_url = data.get("webapp_url", webapp_url)
                    button_label = data.get("button_label", button_label)
                else:
                    logger.warning(
                        "Backend /bot/start failed: %s",
                        r.text if r is not None else "no response"
                    )
            except Exception as e:
                logger.exception("Error calling backend /bot/start: %s", e)

            # ⬇️ ALWAYS append ?ref=... (even if backend failed)
            if ref_code:
                if "?" in webapp_url:
                    webapp_url = f"{webapp_url}&ref={ref_code}"
                else:
                    webapp_url = f"{webapp_url}?ref={ref_code}"

            keyboard = [[InlineKeyboardButton(
                text=button_label,
                web_app=WebAppInfo(url=webapp_url)
            )]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            bot.send_message(
                chat_id=chat_id,
                text=text_to_send,
                reply_markup=reply_markup
            )
            return


        if cmd == '/balance':
            bot.send_message(chat_id=chat_id, text="Use the backend admin endpoints to view balances.")
            return

        # Admin-only commands
        if cmd == '/admin_stats':
            if not is_admin(user_id):
                bot.send_message(chat_id=chat_id, text="Unauthorized")
                return
            r = call_backend('/admin/stats')
            bot.send_message(chat_id=chat_id, text=str(r.json() if r.ok else "Failed"))
            return

        if cmd == '/run_payout':
            if not is_admin(user_id):
                bot.send_message(chat_id=chat_id, text="Unauthorized")
                return
            r = call_backend('/cron/payout', method='POST')
            bot.send_message(chat_id=chat_id, text=str(r.json() if r.ok else "Failed"))
            return

        if cmd == '/recompute_team':
            if not is_admin(user_id):
                bot.send_message(chat_id=chat_id, text="Unauthorized")
                return
            if not args:
                bot.send_message(chat_id=chat_id, text="Usage: /recompute_team <user_id>")
                return
            try:
                uid = int(args[0])
            except:
                bot.send_message(chat_id=chat_id, text="Invalid user_id")
                return
            r = call_backend('/admin/recompute-team', method='POST', json={'user_id': uid})
            bot.send_message(chat_id=chat_id, text=str(r.json() if r.ok else "Failed"))
            return

        # unknown command
        bot.send_message(chat_id=chat_id, text="Unknown command")
    except Exception as e:
        logger.exception("Error handling update: %s", e)


