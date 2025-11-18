import os
import sys
from telegram import Update
from telegram.ext import ContextTypes
# ensure utils import works when running this file directly
sys.path.append(os.path.dirname(__file__))
from utils import is_admin, call_backend

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Hi {user.first_name}! Welcome to Mstc bot. Send /balance to see balances.")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Use the web dashboard to view full balances (demo).")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Unauthorized")
        return
    r = call_backend('/admin/stats')
    if r.ok:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=str(r.json()))
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Failed to fetch stats")

async def run_payout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Unauthorized")
        return
    r = call_backend('/cron/payout', method='POST')
    if r.ok:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Payout run: {r.json()}")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Payout failed")

async def recompute_team_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Unauthorized")
        return
    if not context.args:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: /recompute_team <user_id>")
        return
    try:
        uid = int(context.args[0])
    except:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid user_id")
        return
    r = call_backend('/admin/recompute-team', method='POST', json={'user_id': uid})
    if r.ok:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Recomputed: {r.json()}")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Recompute failed")
