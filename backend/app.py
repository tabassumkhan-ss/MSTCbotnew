import os
import logging
import traceback
import json
import hashlib
import hmac
from urllib.parse import parse_qsl

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from sqlalchemy.exc import SQLAlchemyError
import requests
from dotenv import load_dotenv
from datetime import datetime

from backend.models import Base, engine, SessionLocal, User, Transaction, ReferralEvent,init_db


logger = logging.getLogger(__name__)

init_db()

print("Flask CWD:", os.getcwd())
print("Flask DB URL:", engine.url)


def get_ref_from_payload(data):
    """
    Extract referral ID from the JSON sent by the mini-app.
    Priority:
      1) data["ref"] or data["referrer_id"]
      2) "ref" or "start_param" inside initData (Telegram start_param)
    """
    ref_raw = data.get("ref") or data.get("referrer_id")

    # Step 1: Try from top-level JSON fields
    if not ref_raw:
        init_data = data.get("initData")
        if isinstance(init_data, str):
            try:
                # initData is a querystring-like structure
                pairs = dict(parse_qsl(init_data, keep_blank_values=True))
                ref_raw = pairs.get("ref") or pairs.get("start_param")
            except Exception:
                ref_raw = None

    # Step 2: Convert to int safely
    if not ref_raw:
        return None

    try:
        return int(ref_raw)
    except (TypeError, ValueError):
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

    ref = db.query(User).get(maybe_referrer_id)
    if not ref:
        return

    user.referrer_id = ref.id
    db.commit()
    db.refresh(user)


def get_or_create_user(db, tg_user_raw, maybe_referrer_id):
    """
    Central place to load/create the User AND auto-link referrer.

    tg_user_raw can be:
      - a dict with keys like {"id", "username", "first_name"}
      - a tuple/list that may contain such a dict
      - an int (Telegram user id)
    """
    user_id = None
    username = ""
    first_name = ""

    # Case 1: already a dict
    if isinstance(tg_user_raw, dict):
        user_id = tg_user_raw.get("id")
        username = tg_user_raw.get("username") or ""
        first_name = tg_user_raw.get("first_name") or ""

    # Case 2: tuple or list (e.g. (user_dict, something_else) OR (user_id, ...))
    elif isinstance(tg_user_raw, (tuple, list)):
        # Try to find a dict with "id" inside
        for item in tg_user_raw:
            if isinstance(item, dict) and "id" in item:
                user_id = item.get("id")
                username = item.get("username") or ""
                first_name = item.get("first_name") or ""
                break

        # If still no user_id and first element is an int, treat it as id
        if user_id is None and tg_user_raw:
            first = tg_user_raw[0]
            if isinstance(first, int):
                user_id = first

    # Case 3: raw int → assume it's the Telegram user id
    elif isinstance(tg_user_raw, int):
        user_id = tg_user_raw

    else:
        raise ValueError(f"Unsupported tg_user type: {type(tg_user_raw)} {tg_user_raw!r}")

    if not user_id:
        raise ValueError(f"Telegram user data missing 'id': {tg_user_raw!r}")

    # Look up or create the user
    user = db.query(User).get(user_id)

    if user is None:
        user = User(
            id=user_id,
            username=username,
            first_name=first_name,
            created_at=datetime.utcnow(),
            role="user",          # or "origin" later when conditions met
            self_activated=False, # will become True on first successful deposit
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    # Auto-link referrer only if not already set
    link_referrer_if_needed(db, user, maybe_referrer_id)

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
        upline = db.query(User).get(current.referrer_id)
        if not upline:
            break
        uplines.append((level, upline))
        current = upline
        level += 1
    return uplines


def verify_telegram_init_data(init_data: str):
    """
    Validate Telegram WebApp initData and return:
      (user_id, username, first_name, start_param)
    or (None, None, None, None) if invalid.

    Uses the algorithm from:
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-web-app
    """
    if not init_data:
        return None, None, None, None

    bot_token = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        return None, None, None, None

    # Parse query string into dict
    try:
        data = dict(parse_qsl(init_data, strict_parsing=True))
    except Exception:
        return None, None, None, None

    hash_check = data.pop("hash", None)
    if not hash_check:
        return None, None, None, None

    # Build data_check_string
    data_check_pairs = []
    for key in sorted(data.keys()):
        value = data[key]
        data_check_pairs.append(f"{key}={value}")
    data_check_string = "\n".join(data_check_pairs)

    # Secret key: HMAC-SHA256("WebAppData", bot_token)
    secret_key = hmac.new(
        "WebAppData".encode("utf-8"),
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()

    # HMAC data_check_string with secret_key
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if calculated_hash != hash_check:
        return None, None, None, None

    # If hash is valid, parse user data
    user_str = data.get("user")
    if not user_str:
        return None, None, None, None

    try:
        user = json.loads(user_str)
    except Exception:
        return None, None, None, None

    start_param = data.get("start_param")  # this is the referrer's id (as string)

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
BASE_URL = "https://mstcbotnew.onrender.com"

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
        data = request.get_json() or {}
        init_data = data.get("initData")
        tg_user = verify_telegram_init_data(init_data)

        ref_id = get_ref_from_payload(data)
        user = get_or_create_user(db, tg_user, ref_id)

        logging.info("ME DEBUG raw data: %r", data)
        logging.info(
            "ME DEBUG user_id=%s ref_id=%s user.referrer_id(before)=%s",
            user.id, ref_id, user.referrer_id
        )

        if user.referrer_id is None and ref_id and ref_id != user.id:
            logging.info("ME DEBUG Force-linking referrer: user %s -> %s", user.id, ref_id)
            user.referrer_id = ref_id
            db.commit()
            db.refresh(user)
            logging.info("ME DEBUG user.referrer_id(after)=%s", user.referrer_id)

        resp = {
            "ok": True,
            "user": {
                "id": user.id,
                "first_name": user.first_name,
                "username": user.username,
                "role": user.role,
                "self_activated": user.self_activated,
                "referrer_id": user.referrer_id,
                "total_team_business": float(user.total_team_business or 0),
                "active_origin_count": int(user.active_origin_count or 0),
            },
        }
        return jsonify(resp)
    finally:
        db.close()


@app.route("/webapp/init", methods=["POST"])
def webapp_init():
    """
    Called by telegram_mini_app.html on load (via loadActivationStatus()).

    We treat a user as:
      - "registered" if they have *any* team business or self_activated flag
      - "active" if self_activated is True (Origin or above)
    """
    db = SessionLocal()
    try:
        data = request.get_json() or {}
        init_data = data.get("initData")

        if not init_data:
            return jsonify({"ok": False, "error": "missing_init_data"}), 400

        # Parse Telegram initData 
        tg_user = verify_telegram_init_data(init_data)

        # Try to read ref from body or initData
        ref_id = get_ref_from_payload(data)

        # Get or create the user, and auto-link referrer if possible
        user = get_or_create_user(db, tg_user, ref_id)

        # "Registered" = user has ever done anything (team business or activated)
        total_team_business = float(user.total_team_business or 0.0)
        self_activated = bool(user.self_activated)
        has_registered = bool(self_activated or total_team_business > 0)

        is_active = self_activated  # your Origin/active flag

        resp = {
            "ok": True,
            "has_registered": has_registered,
            "is_active": is_active,
            "total_team_business": total_team_business,
            "active_origin_count": int(getattr(user, "active_origin_count", 0) or 0),
            "role": user.role,
            "self_activated": self_activated,
            "user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "referrer_id": user.referrer_id,
        }
        return jsonify(resp)
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
        user = db.query(User).get(tg_id)
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
# Helpers
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
            parent = db.query(User).get(int(ref_id))
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

        parent = db.query(User).get(parent_id)
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
        parent = db.query(User).get(int(current.referrer_id))
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


def propagate_team_business(db: SessionLocal, user: User, amount: float, became_origin_now: bool):
    """
    Add amount to total_team_business of all uplines.
    Increment active_origin_count of uplines if user became Origin on this deposit.
    """
    visited = set()
    current = user
    while current.referrer_id and current.referrer_id not in visited:
        ref = db.query(User).get(current.referrer_id)
        if not ref:
            break

        visited.add(ref.id)

        ref.total_team_business = (ref.total_team_business or 0.0) + amount

        if became_origin_now:
            ref.active_origin_count = (ref.active_origin_count or 0) + 1

        update_rank(ref)

        current = ref
def distribute_club_bonus(db: SessionLocal, amount: float) -> float:
    """
    Take 2% of this deposit amount and distribute it equally
    among all active club achievers (life_changer / advisor / visionary / creator).

    Returns the total club pool amount taken from this deposit.
    """
    club_cut = round(amount * 0.02, 2)  # 2% of deposit
    if club_cut <= 0:
        return 0.0

    # Find all active club achievers
    achievers = (
        db.query(User)
        .filter(
            User.self_activated == True,
            User.role.in_(["life_changer", "advisor", "visionary", "creator"])
        )
        .all()
    )

    if not achievers:
        # No one to distribute to yet -> pool effectively stays with company
        return 0.0

    # Equal share for each achiever
    per_user = round(club_cut / len(achievers), 2)
    if per_user <= 0:
        return 0.0

    for u in achievers:
        u.club_income = float(u.club_income or 0.0) + per_user
        db.add(u)

    return club_cut

@app.route("/webapp/verify", methods=["POST"])
def webapp_verify():
    db = SessionLocal()
    try:
        data = request.get_json() or {}
        init_data = data.get("initData")
        amount = float(data.get("amount") or 0)

        if not init_data:
            return jsonify({"ok": False, "error": "missing_init_data"}), 400

        if amount <= 0:
            return jsonify({"ok": False, "error": "invalid_amount"}), 400

        # Parse Telegram initData
        tg_user = verify_telegram_init_data(init_data)

        # Referral extraction
        ref_id = get_ref_from_payload(data)

        # Get or create user
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
        # Check if this deposit makes the user Origin for the first time
        became_origin_now = (not user.self_activated and amount >= 20)

        if became_origin_now:
            user.self_activated = True
            # base role; update_rank will refine (life_changer, advisor, etc.)
            user.role = "origin"
            logging.info("User %s activated as Origin", user.id)

        # ---------- SELF BUSINESS ----------
        user.total_team_business = float(user.total_team_business or 0) + amount

        # ---------- TEAM BUSINESS UP THE TREE ----------
        # This adds 'amount' to all uplines' total_team_business
        # and, if became_origin_now=True, increments active_origin_count for each upline.
        propagate_team_business(db, user, amount, became_origin_now)

        # Update THIS user's rank after their own TB change
        update_rank(user)
        club_pool_used = distribute_club_bonus(db, amount)
        logging.info("Club bonus distributed: %s from amount %s", club_pool_used, amount)    

        # ---------- REFERRAL DISTRIBUTION ----------
        # Configurable level percentages
        LEVEL_BONUSES = {
            1: 0.05,  # 5% to Level 1
            2: 0.03,  # 3% to Level 2
            3: 0.02,  # 2% to Level 3
        }

        # Always a LIST (JSON array)
        referral_dist = []

        uplines = get_uplines(db, user, max_levels=3)

        for level, upline in uplines:
            pct = LEVEL_BONUSES.get(level, 0)
            if pct <= 0:
                continue

            bonus_amount = round(amount * pct, 2)

            # Qualification rules by level
            qualifies = False
            role = (upline.role or "user").lower()

            if level == 1:
                # direct sponsor must at least be Origin (self_activated)
                qualifies = bool(upline.self_activated)
            elif level == 2:
                # must be Life Changer or above
                qualifies = role in ("life_changer", "advisor", "visionary", "creator")
            elif level == 3:
                # must be Advisor or above
                qualifies = role in ("advisor", "visionary", "creator")

            if qualifies:
                # Pay bonus to this upline
                referral_dist.append({
                    "level": level,
                    "to_user_id": upline.id,
                    "to_username": upline.username or "",
                    "amount": bonus_amount,
                })

                # Optional: treat this as part of club income
                upline.club_income = float(upline.club_income or 0) + bonus_amount

            else:
                # Bonus for this level goes to company pool
                referral_dist.append({
                    "level": 0,   # 0 means Pool in your UI
                    "to_user_id": None,
                    "to_username": None,
                    "amount": bonus_amount,
                })

        db.commit()

        return jsonify({
            "ok": True,
            "amount": amount,
            "user_id": user.id,
            "self_activated": user.self_activated,
            "role": user.role,
            "referrer_id": user.referrer_id,
            "referral_dist": referral_dist,  # always a list
        })

    except Exception as e:
        db.rollback()
        logging.exception("Error in /webapp/verify")
        return jsonify({"ok": False, "error": "server_error"}), 500

    finally:
        db.close()


@app.route("/debug/downlines/<int:user_id>")
def debug_downlines(user_id):
    db = SessionLocal()
    try:
        user = db.query(User).get(user_id)
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
        user = db.query(User).get(int(user_id))
        ref = db.query(User).get(int(referrer_id))

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
        user = db.query(User).get(user_id)
        if not user:
            return jsonify(ok=False, error="user_not_found"), 404

        user.self_activated = False
        user.role = "user"
        db.commit()

        return jsonify(ok=True, user_id=user.id, self_activated=user.self_activated,role=user.role,)
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

@app.route("/debug/user/<int:user_id>")
def debug_user(user_id):
    db = SessionLocal()
    try:
        user = db.query(User).get(user_id)
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
