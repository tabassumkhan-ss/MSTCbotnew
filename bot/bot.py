import os
import sys
import time
import logging
import threading
import requests

from dotenv import load_dotenv
from telegram import Bot
from apscheduler.schedulers.background import BackgroundScheduler

# local helpers
sys.path.append(os.path.dirname(__file__))
from utils import BACKEND_URL, is_admin, call_backend

load_dotenv()
BOT_TOKEN = "8487241335:AAHfCDzdzZBiedvPAcYbr5_BRqSa8YTaWVs"
POLL_INTERVAL = float(os.getenv('BOT_POLL_INTERVAL', '1.5'))  # seconds between getUpdates
PAYOUT_INTERVAL_MINUTES = int(os.getenv('PAYOUT_INTERVAL_MINUTES', '5'))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set in .env")

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
        if cmd == '/start':
            bot.send_message(chat_id=chat_id, text=f"Hi {from_user.get('first_name','')} — bot running (polling).")
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

def polling_loop(stop_event):
    
    offset = None
    base_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    logger.info("Starting polling loop (interval=%.2fs) via HTTP getUpdates...", POLL_INTERVAL)
    while not stop_event.is_set():
        try:
            params = {'timeout': 30}
            if offset is not None:
                params['offset'] = offset
            resp = requests.get(base_url, params=params, timeout=40)
            if resp.status_code != 200:
                logger.warning("getUpdates non-200: %s %s", resp.status_code, resp.text[:200])
                time.sleep(2)
                continue
            data = resp.json()
            if not data.get('ok'):
                logger.warning("getUpdates returned ok=false: %s", data)
                time.sleep(2)
                continue
            updates = data.get('result', [])
            for upd in updates:
                offset = upd['update_id'] + 1
                handle_command({'message': upd.get('message'), 'edited_message': upd.get('edited_message')})
        except requests.exceptions.ReadTimeout:
            # long poll timed out - normal, continue loop
            continue
        except Exception as e:
            logger.exception("Polling error: %s", e)
            time.sleep(2)
        time.sleep(POLL_INTERVAL)
    logger.info("Polling loop stopped.")

def payout_job():
    try:
        r = call_backend('/cron/payout', method='POST')
        logger.info("Scheduled payout run -> %s", r.status_code if r is not None else 'noresp')
    except Exception as e:
        logger.exception("Scheduled payout failed: %s", e)

def start_scheduler():
    # Using BackgroundScheduler because it works cleanly with plain threads
    scheduler = BackgroundScheduler()
    scheduler.add_job(payout_job, 'interval', minutes=PAYOUT_INTERVAL_MINUTES, id='payout_job', replace_existing=True)
    scheduler.start()
    logger.info("BackgroundScheduler started (payout every %d minutes).", PAYOUT_INTERVAL_MINUTES)
    return scheduler

def main():
    stop_event = threading.Event()

    # start scheduler
    scheduler = start_scheduler()

    # start polling in main thread (so CTRL+C works)
    try:
        polling_loop(stop_event)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        stop_event.set()
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass
        logger.info("Bot exiting.")

if __name__ == '__main__':
    main()