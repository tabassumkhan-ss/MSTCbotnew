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
    Try to read referral id from the incoming JSON.
    We accept either 'ref' or 'referrer_id' fields.
    """
    ref_raw = data.get("ref") or data.get("referrer_id")
    if ref_raw is None:
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
        tg_user = verify_telegram_init_data(init_data)  # your existing function

        # NEW: read referral id from the request
        ref_id = get_ref_from_payload(data)

        # NEW: central helper that creates user + auto-links ref
        user = get_or_create_user(db, tg_user, ref_id)

        # ... build whatever JSON you already return, example:
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
            },
        }
        return jsonify(resp)
    finally:
        db.close()


@app.route("/webapp/init", methods=["POST"])
def webapp_init():
    """
    Called by telegram_mini_app.html on load (via loadActivationStatus()).

    Frontend sends (from your JS):
      { "initData": { "user": { id, username, first_name, ... } } }

    Response:
      - has_registered: False  -> show "Do you want to register?" + Yes/No
      - has_registered: True   -> skip question, directly show full dashboard
    """
    data = request.get_json() or {}
    init_data = data.get("initData") or {}
    user_info = init_data.get("user") or {}

    tg_id = user_info.get("id")
    username = user_info.get("username")
    first_name = user_info.get("first_name")

    if not tg_id:
        return jsonify({"ok": False, "error": "no telegram id"}), 400

    db = SessionLocal()
    try:
        # In your DB, Telegram ID == primary key "id"
        user = db.query(User).get(tg_id)

        if not user:
            # First-time user, not in DB
            return jsonify({
                "ok": True,
                "has_registered": False,
                "is_active": False,
                "user_id": tg_id,
                "username": username,
                "first_name": first_name,
            })

        # User exists → registered
                # User exists → registered
        has_registered = True

        # Treat self_activated as the "active / Origin" flag
        self_activated = bool(getattr(user, "self_activated", False))
        is_active = self_activated

        total_team_business = float(getattr(user, "total_team_business", 0) or 0)
        active_origin_count = int(getattr(user, "active_origin_count", 0) or 0)
        role = getattr(user, "role", "user")



        return jsonify({
            "ok": True,
            "has_registered": has_registered,
            "is_active": is_active,
            "total_team_business": total_team_business,
            "active_origin_count": active_origin_count,
            "role": role,
            "self_activated": self_activated,
            "user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
        })
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

        # Parse Telegram initData and get Telegram user dict
        tg_user = verify_telegram_init_data(init_data)

        # Read referral id from payload (?ref= in mini app URL)
        ref_id = get_ref_from_payload(data)

        # Create/get user and auto-link referrer (if any)
        user = get_or_create_user(db, tg_user, ref_id)

        # ---------- Activation & team business logic ----------
        # mark self_activated if this is their first qualifying deposit
        if not user.self_activated and amount >= 20:
            user.self_activated = True
            # you can set role to "origin" here if that's your rule
            if not user.role:
                user.role = "origin"

        # increment user's own team business
        user.total_team_business = float(user.total_team_business or 0) + amount

        # ---------- Referral distribution (simple Level 1) ----------
        referral_dist = None
        level1_bonus = round(amount * 0.05, 2)  # 5% Origin bonus

        if user.referrer_id:
            referrer = db.query(User).get(user.referrer_id)
            if referrer and referrer.self_activated:
                # Pay Level 1 bonus to referrer
                # (Example: track rewards in some field if you have it)
                if hasattr(referrer, "referral_earnings"):
                    referrer.referral_earnings = float(
                        getattr(referrer, "referral_earnings", 0.0) or 0.0
                    ) + level1_bonus

                referral_dist = {
                    "to": str(referrer.id),
                    "level": 1,
                    "amount": level1_bonus,
                }
            else:
                # Referrer not active → send to company_pool
                referral_dist = {
                    "to": "company_pool",
                    "amount": level1_bonus,
                }
        else:
            # No referrer → send to company_pool
            referral_dist = {
                "to": "company_pool",
                "amount": level1_bonus,
            }

        # ---------- Commit DB changes ----------
        db.commit()

        # ---------- Return JSON response ----------
        return jsonify({
            "ok": True,
            "amount": amount,
            "user_id": user.id,
            "self_activated": user.self_activated,
            "referrer_id": user.referrer_id,
            "referral_dist": referral_dist,
        })

    except Exception as e:
        db.rollback()
        logging.exception("Error in /webapp/verify")
        return jsonify({"ok": False, "error": "server_error"}), 500
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
