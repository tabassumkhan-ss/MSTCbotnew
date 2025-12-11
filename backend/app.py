import os
import logging
import traceback
import json
import hashlib
import hmac
from urllib.parse import parse_qsl

from flask import Flask, request, jsonify, send_from_directory, current_app
from flask_cors import CORS
from sqlalchemy.exc import SQLAlchemyError
import requests
from dotenv import load_dotenv
from datetime import datetime
from typing import Optional  # for safe annotations

from backend.models import Base, engine, SessionLocal, User, Transaction, ReferralEvent, init_db

# Load .env for local dev (harmless on Railway)
load_dotenv()

# Basic logging config so logger.info/debug appear in console/logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Use app.logger where convenient (Flask/Gunicorn friendly)
_debug_key = os.getenv("DEBUG_KEY")
if _debug_key:
    # show only first 6 chars to avoid leaking secret
    app.logger.info("DEBUG_KEY present (first6): %s", _debug_key[:6])
else:
    app.logger.info("DEBUG_KEY NOT present in environment.")

# safe DB url display (mask credentials if you must print)
try:
    db_url = str(engine.url)
    # mask password/creds if present (naive mask)
    if "@" in db_url and ":" in db_url:
        # show driver and host/db only; mask credentials
        parts = db_url.split("@", 1)
        visible = parts[1]
        app.logger.info("Flask DB URL (masked): %s", visible)
    else:
        app.logger.info("Flask DB URL: %s", db_url)
except Exception:
    app.logger.exception("Could not read engine.url")

init_db()

print("Flask CWD:", os.getcwd())
print("Flask DB URL:", engine.url)


def get_ref_from_payload(data: dict) -> Optional[int]:
    """Return ref id (int) or None if not present/invalid."""
    ref = data.get("ref")
    try:
        return int(ref) if ref is not None else None
    except (ValueError, TypeError):
        return None

def link_referrer_if_needed(db, user: User, maybe_referrer_id: int | None):
    """
    Auto-link referral:
      - Only if user has no referrer yet
      - Only if maybe_referrer_id is valid and not self
      - Only if referrer user actually exists
    """
    if user.referrer_id is not None:
        # Already linked, do nothing
        return

    if not maybe_referrer_id:
        return

    if maybe_referrer_id == user.id:
        # No self-referral
        return

    ref = db.get(User, maybe_referrer_id)
    if not ref:
        return

    user.referrer_id = ref.id
    db.commit()
    db.refresh(user)

def get_or_create_user(db, tg_user, ref_id=None):
    # tg_user is expected to be a dict from Telegram WebApp initDataUnsafe.user
    if not isinstance(tg_user, dict):
        raise ValueError(f"tg_user is not a dict: {tg_user!r}")

    tg_user_raw = (
        tg_user.get("id"),
        tg_user.get("username"),
        tg_user.get("first_name"),
        tg_user.get("last_name"),
    )

    if tg_user_raw[0] is None:
        # Don't crash the whole app – let the route handle this.
        raise ValueError(f"Telegram user data missing 'id': {tg_user_raw!r}")

    tg_id = tg_user_raw[0]          # Telegram user ID
    username = tg_user_raw[1]
    first_name = tg_user_raw[2]
    last_name = tg_user_raw[3]

    # You are using User.id as Telegram ID (primary key)
    user = db.query(User).filter_by(id=tg_id).first()

    if user is None:
        # New user: create and set referrer_id if provided
        user = User(
            id=tg_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            created_at=datetime.utcnow(),
            balance_mstc=0.0,
            balance_musd=0.0,
            active=True,
            referrer_id=ref_id,   # 👈 set once on creation
            role="user",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    # Existing user:
    # Update profile info if changed
    if username and user.username != username:
        user.username = username
    if first_name and user.first_name != first_name:
        user.first_name = first_name
    if last_name and getattr(user, "last_name", None) != last_name:
        user.last_name = last_name

    # Only set referrer_id if it's currently None and we got a valid ref_id
    if user.referrer_id is None and ref_id is not None:
        user.referrer_id = ref_id

    db.commit()
    db.refresh(user)
    return user


    # Existing user:
    #  - Optionally update basic profile fields
    #  - Only set referrer_id if it is currently None and we got a non-null ref_id
    if username and user.username != username:
        user.username = username
    if first_name and user.first_name != first_name:
        user.first_name = first_name
    if last_name and user.last_name != last_name:
        user.last_name = last_name

    if user.referrer_id is None and ref_id is not None:
        user.referrer_id = ref_id

    db.commit()
    db.refresh(user)
    return user


def get_uplines(db, user, max_levels=3):
    """
    Walk up the referral tree from `user` and return a list of (level, upline_user).
    level=1 is direct referrer, level=2 is referrer's referrer, etc.
    Stops early if no more uplines.
    """
    uplines = []
    current = user
    level = 1
    while level <= max_levels and current.referrer_id:
        upline = db.get(User, current.referrer_id)
        if not upline:
            break
        uplines.append((level, upline))
        current = upline
        level += 1
    return uplines


def verify_telegram_init_data(init_data: str):
    """
    DEV VERSION (no HMAC check):
    Parse Telegram WebApp initData and return:
      (user_id, username, first_name, start_param)
    or (None, None, None, None) if invalid format.

    This version ONLY parses the data and does NOT validate the signature.
    """
    if not init_data:
        return None, None, None, None

    # Parse query string into dict
    try:
        data = dict(parse_qsl(init_data, strict_parsing=True))
    except Exception:
        return None, None, None, None

    # user is JSON string inside "user" param
    user_str = data.get("user")
    if not user_str:
        return None, None, None, None

    try:
        user = json.loads(user_str)
    except Exception:
        return None, None, None, None

    start_param = data.get("start_param")  # referrer id as string, if present

    return user.get("id"), user.get("username"), user.get("first_name"), start_param


# -------------------------
# Configuration
# -------------------------
load_dotenv()

BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    "8487241335:AAHfCDzdzZBiedvPAcYbr5_BRqSa8YTaWVs"
)
BOT_USERNAME = "mstcrefbot"

# ADMIN_IDS should be iterable; keep as string or parse as needed
ADMIN_IDS = os.getenv("ADMIN_IDS", "7955075357")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "s3cr3t-mstc-2025")

# Used for webapp_url in /bot/start
BASE_URL = "https://mstcbotnew-production.up.railway.app/"

# -------------------------
# App setup
# -------------------------
app = Flask(__name__)

Base.metadata.create_all(bind=engine)

CORS(app)

# -------------------------
# Simple health route
# -------------------------
@app.route("/", methods=["GET"])
def home():
    return "Backend OK", 200


# -------------------------
# WebApp routes
# -------------------------
@app.route("/webapp/me", methods=["POST"])
def webapp_me():
    db = SessionLocal()
    try:
        payload = request.get_json() or {}

        # 1) Get initData from frontend (telegram_mini_app.html sends this)
        init_data = payload.get("initData")
        if not init_data:
            return jsonify({"ok": False, "error": "missing_init_data"}), 400

        # 2) Verify / parse Telegram initData
        #    Assumes verify_telegram_init_data returns:
        #    (telegram_id, username, first_name, start_param)
        try:
            telegram_id, username, first_name, start_param = verify_telegram_init_data(init_data)
        except Exception as e:
            app.logger.exception("verify_telegram_init_data failed in /webapp/me")
            return jsonify({"ok": False, "error": "invalid_init_data"}), 400

        if not telegram_id:
            return jsonify({"ok": False, "error": "invalid_init_data"}), 400

        # 3) Build tg_user dict expected by get_or_create_user
        tg_user = {
            "id": telegram_id,
            "username": username,
            "first_name": first_name,
            # last_name is optional; you can include it if verify_telegram_init_data returns it
        }

        # 4) Determine referral code
        # Prefer explicit "ref" from frontend, then start_param, then helper.
        raw_ref = (
            payload.get("ref")         # from window.currentRef
            or payload.get("start_param")
            or start_param
            or get_ref_from_payload(payload)
        )

        ref_id = None
        if raw_ref:
            try:
                # You are using User.id (PK) as referral code in links
                ref_id = int(raw_ref)
            except (TypeError, ValueError):
                ref_id = None

        # 5) Create or get user, linking referrer if available
        try:
            user = get_or_create_user(db, tg_user, ref_id)
        except ValueError as e:
            app.logger.error(f"/webapp/me invalid telegram user: {e}")
            return jsonify({"ok": False, "error": str(e)}), 400

        return jsonify({
            "ok": True,   # IMPORTANT: your JS checks j.ok
            "user": {
                "id": user.id,                 # this is also their Telegram ID
                "username": user.username,
                "first_name": user.first_name,
                "balance_mstc": user.balance_mstc,
                "balance_musd": user.balance_musd,
                "referrer_id": user.referrer_id,
            }
        })
    except Exception:
        app.logger.exception("Unhandled error in /webapp/me")
        return jsonify({"ok": False, "error": "server_error"}), 500
    finally:
        db.close()


@app.route("/webapp/init", methods=["POST"])
def webapp_init():
    """
    New behaviour:
      - DO NOT create user here.
      - If user already exists -> return their status.
      - If user does not exist -> just return basic info + referrer info
        so frontend can show a 'Register' / 'Join via A' screen.
    """
    db = SessionLocal()
    try:
        data = request.get_json() or {}
        init_data = data.get("initData")
        ref_from_client = data.get("ref")  # 👈 ref sent from JS: window.currentRef

        if not init_data:
            return jsonify({"ok": False, "error": "missing_init_data"}), 400

        # Parse initData into basic user info
        # Assumption: verify_telegram_init_data returns Telegram user id, not DB pk
        telegram_id, username, first_name, start_param = verify_telegram_init_data(init_data)
        if not telegram_id:
            return jsonify({"ok": False, "error": "invalid_init_data"}), 400

        # 🔹 Look up by telegram_id, NOT by primary key
        user = db.query(User).filter_by(telegram_id=str(telegram_id)).first()

        if user:
            total_team_business = float(user.total_team_business or 0.0)
            self_activated = bool(user.self_activated)
            has_registered = bool(self_activated or total_team_business > 0)
            is_active = self_activated

            resp = {
                "ok": True,
                "exists": True,
                "has_registered": has_registered,
                "is_active": is_active,
                "total_team_business": total_team_business,
                "active_origin_count": int(getattr(user, "active_origin_count", 0) or 0),
                "role": user.role,
                "self_activated": self_activated,
                "user_id": user.id,  # DB primary key
                "username": user.username,
                "first_name": user.first_name,
                "referrer_id": user.referrer_id,  # already stored in DB
            }
            return jsonify(resp)

        # 👇 If we reach here, user does NOT exist yet – DO NOT create

        # Decide which ref code to use
        # Prefer explicit ref from client; fall back to Telegram start_param
        raw_ref = ref_from_client or start_param

        referrer_id = None       # DB primary key of referrer
        referrer_username = None

        if raw_ref:
            # Your referral link uses ?startapp=${uid} where uid is DB user.id
            # so raw_ref is most likely that DB primary key.
            # Try interpreting as integer PK first.
            ref_user = None
            try:
                pk = int(raw_ref)
                ref_user = db.get(User, pk)
            except (TypeError, ValueError):
                ref_user = None

            # If that fails, you could optionally fall back to telegram_id:
            if not ref_user:
                ref_user = db.query(User).filter_by(telegram_id=str(raw_ref)).first()

            if ref_user:
                referrer_id = ref_user.id
                referrer_username = ref_user.username or ref_user.first_name

        return jsonify({
            "ok": True,
            "exists": False,
            "has_registered": False,
            "is_active": False,
            # For not-yet-created users, send Telegram id separately from DB id
            "user_id": None,
            "telegram_id": str(telegram_id),
            "username": username,
            "first_name": first_name,
            "referrer_id": referrer_id,
            "referrer_username": referrer_username,
        })
    except Exception:
        logging.exception("Error in /webapp/init")
        return jsonify({"ok": False, "error": "server_error"}), 500
    finally:
        db.close()

@app.post("/bot/start")
def bot_start():
    """
    Called by your bot.py /start handler via call_backend('/bot/start').

    Request JSON:
      {
        "telegram_id": <int>,
        "username": "...",
        "first_name": "...",
        "ref_code": <optional>
      }

    Response JSON:
      {
        "message": "...",
        "button_label": "...",
        "webapp_url": "https://.../static/telegram_mini_app.html"
      }
    """
    data = request.get_json() or {}

    tg_id = data.get("telegram_id")
    username = data.get("username")
    first_name = data.get("first_name")
    ref_code = data.get("ref_code")

    if not tg_id:
        return jsonify({"ok": False, "error": "missing_telegram_id"}), 400

    db = SessionLocal()
    try:
        user = db.get(User, tg_id)
        is_new = False
        changed = False

        if not user:
            is_new = True
            user = User(
                id=tg_id,
                username=username or "",
                first_name=first_name or "",
                role="user",
                self_activated=False,
                balance_musd=0.0,
                balance_mstc=0.0,
            )
            # Set referrer if provided
            if ref_code:
                try:
                    user.referrer_id = int(ref_code)
                except Exception:
                    pass
            db.add(user)
            changed = True
        else:
            # If this user has no referrer yet and a ref_code is provided, set it now
            if ref_code and not getattr(user, "referrer_id", None):
                try:
                    user.referrer_id = int(ref_code)
                    changed = True
                except Exception:
                    pass

            # Update username/first_name if changed
            if username and user.username != username:
                user.username = username
                changed = True
            if first_name and user.first_name != first_name:
                user.first_name = first_name
                changed = True

            if changed:
                db.add(user)

        if changed:
            db.commit()
            db.refresh(user)

        if is_new:
            message = f"Welcome {first_name or ''}! Tap below to open the MSTC deposit mini app."
            button_label = "Register / Open Mini App"
        else:
            message = f"Welcome back, {first_name or ''}! Tap below to continue."
            button_label = "Open Deposit Mini App"

        webapp_url = f"{BASE_URL}/static/telegram_mini_app.html"

        return jsonify({
            "ok": True,
            "message": message,
            "button_label": button_label,
            "webapp_url": webapp_url,
        })
    finally:
        db.close()



# -------------------------
# Helpers
# -------------------------
# -------------------------
def _get_referrer_chain(db, user, max_levels=3):
    if db is None or user is None:
        return []

    chain = []
    current = user
    for _ in range(max_levels):
        ref_id = getattr(current, "referrer_id", None)
        if not ref_id:
            break
        try:
            parent = db.get(User, int(ref_id))
        except Exception:
            break
        if not parent:
            break
        chain.append(parent)
        current = parent
    return chain


def is_origin(user):
    try:
        return bool(user.self_activated) or (getattr(user, "role", "") == "origin")
    except Exception:
        return False


def is_life_changer(user):
    try:
        return (
            float(getattr(user, "total_team_business", 0.0)) >= 1000.0
            and int(getattr(user, "active_origin_count", 0)) >= 10
        )
    except Exception:
        return False


def _increment_active_origins_for_upline(db, new_origin_user):
    """
    Called exactly once when a user becomes Origin (self_activated=True for the first time).
    Walks up the referrer chain and increments active_origin_count for each upline.
    """
    current = new_origin_user
    visited = set()

    while getattr(current, "referrer_id", None):
        try:
            parent_id = int(current.referrer_id)
        except Exception:
            break

        # avoid loops just in case
        if parent_id in visited:
            break
        visited.add(parent_id)

        parent = db.get(User, parent_id)
        if not parent:
            break

        try:
            current_count = int(getattr(parent, "active_origin_count", 0) or 0)
            parent.active_origin_count = current_count + 1
            db.add(parent)
        except Exception:
            pass

        current = parent


def credit_team_business(db, user, amount):
    current = user
    while getattr(current, "referrer_id", None):
        parent = db.get(User, int(current.referrer_id))
        if not parent:
            break
        try:
            parent.total_team_business = float(parent.total_team_business or 0.0) + float(amount)
            db.add(parent)
        except Exception:
            pass
        current = parent

def update_rank(user: User):
    """Update user.role based on total_team_business, active_origin_count and self_activated."""
    
    total = user.total_team_business or 0.0
    active_origins = user.active_origin_count or 0

    if total >= 100000:
        user.role = "creator"
    elif total >= 25000:
        user.role = "visionary"
    elif total >= 5000:
        user.role = "advisor"
    elif total >= 1000 and active_origins >= 10:
        user.role = "life_changer"
    elif user.self_activated:
        user.role = "origin"
    else:
        if not user.role:
            user.role = "user"

# Role-based percentage for Level 1 (direct sponsor) commissions
ROLE_LEVEL1_PCT = {
    "origin": 0.05,        # 5%
    "life_changer": 0.10,  # 10%
    "advisor": 0.15,       # 15%
    "visionary": 0.20,     # 20%
    "creator": 0.25,       # 25%
}


def propagate_team_business(db: SessionLocal, user: User, amount: float, became_origin_now: bool):
    """
    Add amount to total_team_business of all uplines.
    Increment active_origin_count of uplines if user became Origin on this deposit.
    """
    visited = set()
    current = user
    while current.referrer_id and current.referrer_id not in visited:
        ref = db.get(User, current.referrer_id)
        if not ref:
            break

        visited.add(ref.id)

        ref.total_team_business = (ref.total_team_business or 0.0) + amount

        if became_origin_now:
            ref.active_origin_count = (ref.active_origin_count or 0) + 1

        update_rank(ref)

        current = ref

def distribute_club_bonus(db: SessionLocal, amount: float) -> float:
    club_cut = round(amount * 0.02, 2)  # 2% of deposit
    if club_cut <= 0:
        return 0.0

    achievers = (
        db.query(User)
        .filter(
            User.self_activated == True,
            User.role.in_(["life_changer", "advisor", "visionary", "creator"])
        )
        .all()
    )

    if not achievers:
        # No club achievers yet -> whole 2% effectively remains with company
        add_to_company_pool(db, club_cut)
        return club_cut

    per_user = round(club_cut / len(achievers), 2)
    if per_user <= 0:
        # If it's too small to split, also treat as company pool
        add_to_company_pool(db, club_cut)
        return club_cut

    distributed_total = 0.0
    for u in achievers:
        u.club_income = float(u.club_income or 0.0) + per_user
        db.add(u)
        distributed_total += per_user

    # Any tiny leftover from rounding goes to company pool
    leftover = round(club_cut - distributed_total, 2)
    if leftover > 0:
        add_to_company_pool(db, leftover)

    return club_cut


# Special internal user id for the company pool
# Use a reserved unlikely ID to avoid collision with real Telegram user IDs
COMPANY_USER_ID = -999999999  # reserved internal id


def get_company_user(db: SessionLocal) -> User:
    """
    Ensure there is a special 'company_pool' user in the User table.
    We store all company pool funds in this user's balances.
    """
    company = db.get(User, COMPANY_USER_ID)
    if not company:
        company = User(
            id=COMPANY_USER_ID,
            username="company_pool",
            first_name="Company",
            role="company",
            self_activated=False,
            created_at=datetime.utcnow(),
            balance_musd=0.0,
            balance_mstc=0.0,
        )
        db.add(company)
        db.commit()
        db.refresh(company)
    return company


def add_to_company_pool(db: SessionLocal, amount: float, *, commit: bool = False):
    """
    Add the given amount to the company pool balance (MUSD).
    By default it does not commit; pass commit=True to commit immediately.
    """
    amount = float(amount or 0.0)
    if amount <= 0:
        return

    company = get_company_user(db)
    company.balance_musd = float(company.balance_musd or 0.0) + amount
    db.add(company)
    if commit:
        db.commit()
        db.refresh(company)

@app.route("/webapp/save_wallet", methods=["POST"])
def webapp_save_wallet():
    data = request.get_json(silent=True) or {}
    tg_id = data.get("telegram_id")
    wallet_address = data.get("wallet_address")

    if not tg_id or not wallet_address:
        return jsonify({"ok": False, "error": "missing_data"}), 400

    db = SessionLocal()
    try:
        user = db.get(User, int(tg_id))
        if not user:
            return jsonify({"ok": False, "error": "user_not_found"}), 404

        user.wallet_address = wallet_address
        db.add(user)
        db.commit()
        db.refresh(user)

        return jsonify({"ok": True, "wallet_address": wallet_address})
    except Exception as e:
        db.rollback()
        logging.exception("Error in /webapp/save_wallet")
        return jsonify({"ok": False, "error": "server_error", "message": str(e)}), 500
    finally:
        db.close()

@app.route("/webapp/register", methods=["POST"])
def webapp_register():
    """
    Called when user explicitly taps 'Register' in the mini-app.
    This is the ONLY place where we actually create the User from WebApp.
    Also: if a user already exists and has no referrer yet, we attach it once.
    """
    db = SessionLocal()
    try:
        data = request.get_json() or {}
        init_data = data.get("initData")

        if not init_data:
            return jsonify({"ok": False, "error": "missing_init_data"}), 400

        # You already have this helper in your project
        uid, username, first_name, start_param = verify_telegram_init_data(init_data)
        if not uid:
            return jsonify({"ok": False, "error": "invalid_init_data"}), 400

        # 🔹 Check if user already exists
        existing = db.get(User, uid)
        if existing:
            # ✅ If user has no referrer yet, try to set from payload/start_param
            if existing.referrer_id is None:
                ref_id = get_ref_from_payload(data)  # must read "ref" from JSON
                if not ref_id and start_param:
                    try:
                        ref_id = int(start_param)
                    except (TypeError, ValueError):
                        ref_id = None

                # avoid self-referral
                if ref_id and ref_id != existing.id:
                    link_referrer_if_needed(db, existing, ref_id)
                    db.refresh(existing)

            total_team_business = float(existing.total_team_business or 0.0)
            self_activated = bool(existing.self_activated)
            has_registered = bool(self_activated or total_team_business > 0)
            is_active = self_activated

            return jsonify({
                "ok": True,
                "registered": False,
                "exists": True,
                "has_registered": has_registered,
                "is_active": is_active,
                "user_id": existing.id,
                "username": existing.username,
                "first_name": existing.first_name,
                "referrer_id": existing.referrer_id,
                "role": existing.role,
            })

        # 🔹 New user: build basic tg_user
        tg_user = {
            "id": uid,
            "username": username,
            "first_name": first_name,
        }

        # 🔹 Referral logic (for NEW user)
        ref_id = get_ref_from_payload(data)
        if not ref_id and start_param:
            try:
                ref_id = int(start_param)
            except (TypeError, ValueError):
                ref_id = None

        # avoid self-referral
        if ref_id == uid:
            ref_id = None

        # Now we actually create user
        user = get_or_create_user(db, tg_user, ref_id)
        link_referrer_if_needed(db, user, ref_id)

        total_team_business = float(user.total_team_business or 0.0)
        self_activated = bool(user.self_activated)
        has_registered = bool(self_activated or total_team_business > 0)
        is_active = self_activated

        return jsonify({
            "ok": True,
            "registered": True,
            "exists": True,
            "has_registered": has_registered,
            "is_active": is_active,
            "user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "referrer_id": user.referrer_id,
            "role": user.role,
        })
    except Exception:
        logging.exception("Error in /webapp/register")
        db.rollback()
        return jsonify({"ok": False, "error": "server_error"}), 500
    finally:
        db.close()


@app.route("/webapp/verify", methods=["POST"])
def webapp_verify():
    db = SessionLocal()
    try:
        # Safer JSON load + log
        data = request.get_json(silent=True) or {}
        logging.info("WEBAPP_VERIFY payload: %r", data)

        # initData from Telegram WebApp (raw string) + amount
        init_data = data.get("initData") or data.get("init_data") or ""
        raw_amount = (
            data.get("amount")
            or data.get("amount_mstc")
            or data.get("deposit_amount")
        )

        try:
            amount = float(raw_amount or 0)
        except (TypeError, ValueError):
            amount = 0.0

        if not init_data:
            return jsonify({
                "ok": False,
                "error": "missing_init_data",
                "message": "missing_init_data",
            }), 400

        if amount <= 0:
            return jsonify({
                "ok": False,
                "error": "invalid_amount",
                "message": "invalid_amount",
            }), 400

        # ---------- Parse Telegram initData ----------
        uid, username, first_name, start_param = verify_telegram_init_data(init_data)

        if not uid:
            # Hash invalid or user missing in initData
            return jsonify({
                "ok": False,
                "error": "invalid_init_data",
                "message": "invalid_init_data",
            }), 400

        # Build tg_user dict for get_or_create_user()
        tg_user = {
            "id": uid,
            "username": username,
            "first_name": first_name,
        }

        # ---------- Referral extraction ----------
        ref_id = get_ref_from_payload(data)
        if not ref_id and start_param:
            try:
                ref_id = int(start_param)
            except (TypeError, ValueError):
                ref_id = None

        # ---------- Get or create user ----------
        user = get_or_create_user(db, tg_user, ref_id)

        logging.info("VERIFY DEBUG raw data: %r", data)
        logging.info(
            "VERIFY DEBUG user_id=%s ref_id=%s user.referrer_id(before)=%s",
            user.id, ref_id, user.referrer_id
        )

        # Force-link if needed
        if user.referrer_id is None and ref_id and ref_id != user.id:
            logging.info("Force-linking referrer: user %s -> %s", user.id, ref_id)
            user.referrer_id = ref_id
            db.commit()
            db.refresh(user)
            logging.info(
                "VERIFY DEBUG user.referrer_id(after)=%s", user.referrer_id
            )

        # ---------- ACTIVATION ----------
        became_origin_now = (not user.self_activated and amount >= 20)

        if became_origin_now:
            user.self_activated = True
            user.role = "origin"
            logging.info("User %s activated as Origin", user.id)

        # ---------- SELF BUSINESS ----------
        user.total_team_business = float(user.total_team_business or 0) + amount

        # ---------- TEAM BUSINESS UP THE TREE ----------
        propagate_team_business(db, user, amount, became_origin_now)

        # Update THIS user's rank
        update_rank(user)

        # ---------- CLUB BONUS (2%) ----------
        club_pool_used = distribute_club_bonus(db, amount)
        logging.info(
            "Club bonus distributed: %s from amount %s", club_pool_used, amount
        )

        # ---------- REFERRAL DISTRIBUTION ----------
        LEVEL_BONUSES_FIXED = {
            2: 0.03,  # 3% to Level 2
            3: 0.02,  # 2% to Level 3
        }

        referral_dist = []
        uplines = get_uplines(db, user, max_levels=3)

        for level, upline in uplines:
            # Determine percentage
            if level == 1:
                role_key = (upline.role or "user").lower()
                pct = ROLE_LEVEL1_PCT.get(role_key, 0.0)
            else:
                pct = LEVEL_BONUSES_FIXED.get(level, 0.0)

            if pct <= 0:
                continue

            bonus_amount = round(amount * pct, 2)

            # Qualification rules
            qualifies = False
            role = (upline.role or "user").lower()

            if level == 1:
                qualifies = bool(upline.self_activated)
            elif level == 2:
                qualifies = role in ("life_changer", "advisor", "visionary", "creator")
            elif level == 3:
                qualifies = role in ("advisor", "visionary", "creator")

            if qualifies:
                referral_dist.append({
                    "level": level,
                    "to_user_id": upline.id,
                    "to_username": upline.username or "",
                    "amount": bonus_amount,
                })

                upline.club_income = float(upline.club_income or 0) + bonus_amount
                db.add(upline)
            else:
                add_to_company_pool(db, bonus_amount)

                referral_dist.append({
                    "level": 0,   # 0 means Pool
                    "to_user_id": None,
                    "to_username": None,
                    "amount": bonus_amount,
                })

        db.commit()

        return jsonify({
            "ok": True,
            "message": "deposit_processed",
            "amount": amount,
            "user_id": user.id,
            "self_activated": user.self_activated,
            "role": user.role,
            "referrer_id": user.referrer_id,
            "referral_dist": referral_dist,
        }), 200

    except Exception as e:
        db.rollback()
        logging.exception("Error in /webapp/verify")
        # IMPORTANT: include a 'message' so frontend never shows "Unknown error" silently
        return jsonify({
            "ok": False,
            "error": "server_error",
            "message": str(e),
        }), 500
    finally:
        db.close()


@app.route("/debug/downlines/<int:user_id>")
def debug_downlines(user_id):
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            return jsonify({"exists": False, "error": "user_not_found"})

        # direct downlines
        direct = db.query(User).filter(User.referrer_id == user_id).all()

        return jsonify({
            "exists": True,
            "user": {
                "id": user.id,
                "first_name": user.first_name,
                "username": user.username,
                "role": user.role,
                "self_activated": user.self_activated,
                "referrer_id": user.referrer_id,
                "total_team_business": float(user.total_team_business or 0),
            },
            "direct_downlines": [
                {
                    "id": d.id,
                    "first_name": d.first_name,
                    "username": d.username,
                    "role": d.role,
                    "self_activated": d.self_activated,
                    "referrer_id": d.referrer_id,
                    "total_team_business": float(d.total_team_business or 0),
                }
                for d in direct
            ],
            "direct_downline_count": len(direct),
        })
    finally:
        db.close()


@app.route("/debug/link_referrer", methods=["POST"])
def debug_link_referrer():
    """
    DEBUG ONLY: Manually set referrer_id for a user.
    Body: { "user_id": <downline_id>, "referrer_id": <upline_id> }
    """
    data = request.get_json(force=True) or {}
    user_id = data.get("user_id")
    referrer_id = data.get("referrer_id")

    if not user_id or not referrer_id:
        return jsonify(ok=False, error="missing_ids"), 400

    db = SessionLocal()
    try:
        user = db.get(User, int(user_id))
        ref = db.get(User, int(referrer_id))

        if not user or not ref:
            return jsonify(ok=False, error="not_found"), 404

        user.referrer_id = ref.id
        db.commit()

        return jsonify(ok=True, user_id=user.id, referrer_id=ref.id)
    except Exception as e:
        db.rollback()
        print("Error in /debug/link_referrer:", e)
        traceback.print_exc()
        return jsonify(ok=False, error="db_error", detail=str(e)), 500
    finally:
        db.close()

        
@app.route("/debug/list_users", methods=["GET"])
def debug_list_users():
    """DEBUG: list users in the current DB."""
    db = SessionLocal()
    try:
        users = db.query(User).all()
        data = []
        for u in users:
            data.append({
                "id": u.id,
                "username": u.username,
                "first_name": u.first_name,
                "self_activated": u.self_activated,
                "referrer_id": u.referrer_id,
                "total_team_business": u.total_team_business,
                "active_origin_count": u.active_origin_count,
                "role": u.role,
            })
        return jsonify(ok=True, users=data)
    finally:
        db.close()

@app.route("/debug/company_pool", methods=["GET"])
def debug_company_pool():
    db = SessionLocal()
    try:
        company = db.get(User, COMPANY_USER_ID)
        if not company:
            return jsonify(ok=True, exists=False, balance_musd=0.0, balance_mstc=0.0)

        return jsonify(
            ok=True,
            exists=True,
            user_id=company.id,
            username=company.username,
            role=company.role,
            balance_musd=float(company.balance_musd or 0.0),
            balance_mstc=float(company.balance_mstc or 0.0),
            club_income=float(company.club_income or 0.0) if hasattr(company, "club_income") else 0.0,
        )
    finally:
        db.close()
        @app.route("/debug/simulate_deposit", methods=["POST"])
        def debug_simulate_deposit():
         """
    POST JSON:
    {
      "telegram_id": 7955075358,
      "amount": 20.0,
      "tx_musd": "TX1",
      "tx_mstc": "TX2",
      "ref": 7955075357   # optional: DB id of referrer
    }
    """
    try:
        from backend.models import SessionLocal, User, Transaction
    except Exception as e:
        app.logger.exception("Import error in simulate_deposit")
        return jsonify({"ok": False, "error": f"import_error: {e}"}), 500

    db = SessionLocal()
    try:
        data = request.get_json() or {}
        tg_id = data.get("telegram_id")
        amount = float(data.get("amount") or 0)
        tx1 = data.get("tx_musd")
        tx2 = data.get("tx_mstc")
        ref = data.get("ref")

        if not tg_id or amount <= 0 or not tx1 or not tx2:
            return jsonify({"ok": False, "error": "missing_fields"}), 400

        # Build minimal tg_user dict for get_or_create_user
        tg_user = {"id": tg_id, "username": None, "first_name": None}

        # Ensure user exists and link ref if provided
        user = get_or_create_user(db, tg_user, ref)

        # Create a Transaction row (adjust field names if different)
        tx = Transaction(
            user_id=user.id,
            amount=amount,
            tx_musd=tx1,
            tx_mstc=tx2,
            created_at=datetime.utcnow()
        )
        db.add(tx)

        # Example: update balances the same way your app does (adjust if needed)
        user.balance_musd = (user.balance_musd or 0) + amount * 0.7
        user.balance_mstc = (user.balance_mstc or 0) + amount * 0.3

        db.commit()
        db.refresh(user)

        # Optionally run whatever referral distribution logic you have here
        # For now return basic success and the user's referrer
        return jsonify({
            "ok": True,
            "message": "simulated_deposit_ok",
            "user_id": user.id,
            "referrer_id": user.referrer_id
        })
    except Exception as e:
        app.logger.exception("Error in /debug/simulate_deposit")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()
        


# -------------------------
# Static mini-app file (optional helper)
# -------------------------
@app.route("/telegram_mini_app.html")
def serve_mini_app():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "telegram_mini_app.html"
    )
@app.route("/debug/reset_origin/<int:user_id>")
def debug_reset_origin(user_id):
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            return jsonify(ok=False, error="user_not_found"), 404

        user.self_activated = False
        user.role = "user"
        db.commit()

        return jsonify(ok=True, user_id=user.id, self_activated=user.self_activated,role=user.role,)
    finally:
        db.close()

@app.route("/debug/latest-users")
def debug_latest_users():
    try:
        from backend.models import SessionLocal, User
    except Exception as e:
        app.logger.exception("Import error in /debug/latest-users")
        return jsonify({"ok": False, "error": f"import_error: {e}"}), 500

    db = SessionLocal()
    try:
        rows = (
            db.query(User.id, User.referrer_id)
            .order_by(User.id.desc())
            .limit(10)
            .all()
        )
        out = [
            {
                "id": r.id,                 # this is your Telegram ID
                "telegram_id": r.id,        # exposing it also as telegram_id for clarity
                "referrer_id": r.referrer_id,
            }
            for r in rows
        ]
        return jsonify({"ok": True, "users": out})
    except Exception as e:
        app.logger.exception("Error in /debug/latest-users")
        return jsonify({"ok": False, "error": f"server_error: {e}"}), 500
    finally:
        db.close()


# -------------------------
# Telegram webhook handler
# -------------------------
@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    req_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if WEBHOOK_SECRET and req_secret != WEBHOOK_SECRET:
        logging.warning("Invalid/missing webhook secret: %s", req_secret)
        return jsonify({"ok": False, "error": "invalid_secret"}), 401

    update = request.get_json(silent=True)
    if update is None:
        logging.warning("No JSON payload received on /webhook")
        return jsonify({"ok": False, "error": "no_json"}), 400

    logging.info("Telegram update received: %s", update)

    try:
        if "message" in update:
            msg = update["message"]
            chat_id = msg["chat"]["id"]
            text = msg.get("text", "")
            requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                params={"chat_id": chat_id, "text": "Thanks — received: " + (text or "<no text>")},
                timeout=5,
            )
        elif "callback_query" in update:
            cq = update["callback_query"]
            cid = cq["message"]["chat"]["id"]
            requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                params={"chat_id": cid, "text": "Callback received"},
                timeout=5,
            )
    except Exception as e:
        logging.exception("Error handling update: %s", e)

    return jsonify({"ok": True}), 200

import os

# Temporary debug endpoint — simulate a deposit and return referral distribution.
# SECURITY: disabled by default. To enable set environment var ENABLE_DEBUG_ENDPOINT=1 and DEBUG_KEY to a strong secret.
@app.route("/debug/simulate_deposit", methods=["POST"])
def debug_simulate_deposit():
    # log request
    app.logger.info("simulate_deposit attempt headers=%s body=%s", dict(request.headers), request.get_data(as_text=True))

    if not check_debug_key():
        return jsonify({"error":"invalid_debug_key","ok":False}), 401

    payload = request.get_json(silent=True) or {}
    # permissive id parsing
    tg_id = payload.get("user_id") or payload.get("telegram_id") or (payload.get("user") or {}).get("id")
    try:
        tg_id = int(tg_id) if tg_id is not None else None
    except (TypeError, ValueError):
        tg_id = None

    # amount parsing
    try:
        amount = float(payload.get("amount")) if payload.get("amount") is not None else None
    except (TypeError, ValueError):
        amount = None

    if not tg_id or amount is None:
        app.logger.warning("simulate_deposit missing user or amount: tg_id=%s amount=%s", tg_id, amount)
        return jsonify({"error":"missing_user_or_amount","ok":False}), 400

    tx_musd = payload.get("tx_musd")  # external id for the incoming deposit (idempotency)
    tx_mstc = payload.get("tx_mstc")

    db = SessionLocal()
    try:
        user = db.query(User).filter_by(telegram_id=tg_id).first()
        if not user:
            app.logger.warning("simulate_deposit user not found tg_id=%s", tg_id)
            return jsonify({"error":"user_not_found","ok":False}), 404

        # idempotency: if tx_musd provided and already recorded, return success
        if tx_musd:
            existing = db.query(Transaction).filter_by(external_id=str(tx_musd)).first()
            if existing:
                app.logger.info("simulate_deposit: external_id %s already processed -> returning existing state", tx_musd)
                # refresh user to reflect latest state and return
                db.refresh(user)
                resp = {"ok": True, "user_id": user.id, "user": {"id": user.id, "role": user.role, "self_activated": user.self_activated, "total_team_business": user.total_team_business}, "amount": amount}
                return jsonify(resp), 200

        # ----- APPLY BALANCE / BUSINESS UPDATES (keeps existing logic)
        # If you already have logic elsewhere to update user and company pool, call it here.
        # For safety in debug route we do minimal updates: add to user's total_team_business and mark origin/self_activated as in your prior flow.
        became_origin_now = False
        if not getattr(user, "self_activated", False):
            user.self_activated = True
            became_origin_now = True
        # update team business
        user.total_team_business = (user.total_team_business or 0.0) + amount

        # Update company pool user if you have one (example uses id -999999999)
        company_pool = db.get(User, -999999999)
        if company_pool:
            company_pool.balance_musd = (company_pool.balance_musd or 0.0) + (amount * 0.72)  # example split used earlier
        else:
            app.logger.debug("simulate_deposit: company_pool user not found with id -999999999")

        # ---- create transaction audit record for MUSD deposit
        deposit_tx = Transaction(
            user_id=user.id,
            amount=amount,
            currency="MUSD",
            type="deposit",
            external_id=str(tx_musd) if tx_musd else None,
            created_at=datetime.utcnow()
        )
        db.add(deposit_tx)

        # ---- optional: create MSTC credit transaction if tx_mstc provided
        if tx_mstc:
            credit_tx = Transaction(
                user_id=user.id,
                amount=0.0,  # set real MSTC amount if your conversion exists
                currency="MSTC",
                type="credit_mstc",
                external_id=str(tx_mstc),
                created_at=datetime.utcnow()
            )
            db.add(credit_tx)

        # commit all changes
        db.commit()

        # refresh objects for response
        db.refresh(user)
        response = {
            "ok": True,
            "amount": amount,
            "became_origin_now": became_origin_now,
            "company_pool": {"id": getattr(company_pool, "id", None), "balance_musd": getattr(company_pool, "balance_musd", None)},
            "referral_dist": [],
            "user": {"id": user.id, "role": user.role, "self_activated": user.self_activated, "total_team_business": user.total_team_business},
            "user_id": user.id
        }
        return jsonify(response), 200

    except Exception:
        db.rollback()
        app.logger.exception("simulate_deposit: failed to persist transactions/referrals")
        return jsonify({"error":"server_error","ok":False}), 500

    finally:
        db.close()

@app.route("/debug/user/<int:user_id>")
def debug_user(user_id):
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            return jsonify({"exists": False})

        return jsonify({
            "exists": True,
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "self_activated": user.self_activated,
            "role": user.role,
            "referrer_id": user.referrer_id,
            "total_team_business": float(user.total_team_business or 0)
        })
    finally:
        db.close()


# -------------------------
# Entrypoint
# -------------------------
if __name__ == "__main__":
    import os
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("backend.app")
    logger.info("Starting backend.app entrypoint (pid=%s)", os.getpid())

    # Render will set PORT; locally it will default to 8001
    port = int(os.environ.get("PORT", 8001))
    host = "0.0.0.0"          # IMPORTANT: must be 0.0.0.0 for Render
    debug = False             # keep False in production

    logger.info("Flask run -> host=%s port=%s debug=%s", host, port, debug)
    app.run(host=host, port=port, debug=debug)
