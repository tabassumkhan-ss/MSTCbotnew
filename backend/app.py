import os
import logging
import json
from urllib.parse import parse_qsl
from backend.models import Base, engine
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from sqlalchemy.exc import SQLAlchemyError
import requests
from dotenv import load_dotenv

# Resilient imports: works both when running as script and as package module
try:
    # when running as package: python -m backend.app
    from models import SessionLocal, User, Transaction, ReferralEvent, Base, engine
    from .utils import verify_telegram_initdata
except Exception:
    # when running as script: python backend\app.py
    from models import SessionLocal, User, Transaction, ReferralEvent
    from utils import verify_telegram_initdata

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
    """
    Verify Telegram WebApp initData, ensure user exists in DB, and return user info.
    Frontend sends: { initData: Telegram.WebApp.initData } (string).
    """
    data = request.get_json(force=True)
    init_data_str = data.get("initData", "")

    if not init_data_str:
        return jsonify({"ok": False, "error": "missing_initData"}), 400

    # 1) Parse Telegram initData string → dict
    pairs = parse_qsl(init_data_str, strict_parsing=True)
    init_data = {}
    for k, v in pairs:
        if k in ("user", "chat"):
            try:
                init_data[k] = json.loads(v)
            except Exception:
                init_data[k] = {}
        else:
            init_data[k] = v

    if "user" not in init_data:
        return jsonify({"ok": False, "error": "missing_initData.user"}), 400

    # 2) Verify signature
        # Verify Telegram signature (TEMPORARILY DISABLED FOR TESTING)
    verified = True  # TODO: re-enable verify_telegram_initdata in production

    if not verified:
        return jsonify({"ok": False, "error": "verify failed"}), 403

    tg_user = init_data["user"]
    user_id = int(tg_user.get("id"))

    db = SessionLocal()
    try:
        user = db.query(User).get(user_id)
        if not user:
            user = User(
                id=user_id,
                username=tg_user.get("username") or "",
                first_name=tg_user.get("first_name") or "",
                role="user",
                self_activated=False,
                balance_musd=0.0,
                balance_mstc=0.0,
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        resp_user = {
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "balance_musd": float(user.balance_musd or 0.0),
            "balance_mstc": float(user.balance_mstc or 0.0),
            "role": user.role,
            "total_team_business": float(getattr(user, "total_team_business", 0.0) or 0.0),
            "active_origin_count": int(getattr(user, "active_origin_count", 0) or 0),
        }
        return jsonify({"ok": True, "user": resp_user, "bot_username": BOT_USERNAME})
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




# -------------------------
# Main verify route (deposit)
# -------------------------
@app.route("/webapp/verify", methods=["POST"])
def webapp_verify():
    """
    Verify Telegram WebApp initData + deposit info, then:
    - credit user balances
    - apply referral distribution
    - record transactions
    """
    data = request.get_json(force=True)
    init_data_str = data.get("initData", "")
    amount = data.get("amount")

    if not init_data_str:
        return jsonify({"ok": False, "error": "missing_initData"}), 400

    # Parse initData string → dict
    pairs = parse_qsl(init_data_str, strict_parsing=True)
    init_data = {}
    for k, v in pairs:
        if k in ("user", "chat"):
            try:
                init_data[k] = json.loads(v)
            except Exception:
                init_data[k] = {}
        else:
            init_data[k] = v

    if "user" not in init_data:
        return jsonify({"ok": False, "error": "missing_initData.user"}), 400

    # Verify Telegram signature
        # 2) Verify signature (TEMPORARILY DISABLED FOR TESTING)
    verified = True  # TODO: re-enable verify_telegram_initdata in production

    if not verified:
        return jsonify({"ok": False, "error": "verify failed"}), 403


    tg_user = init_data["user"]
    user_id = int(tg_user["id"])

    # Validate amount
    try:
        amount = float(amount)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_amount"}), 400

    MIN_DEPOSIT = 20.0
    STEP = 10.0
    if amount < MIN_DEPOSIT:
        return jsonify({"ok": False, "error": "min_deposit"}), 400
    if amount != MIN_DEPOSIT and ((amount - MIN_DEPOSIT) % STEP) != 0:
        return jsonify({"ok": False, "error": "invalid_step"}), 400

    # monetary split
    MSTC_PERCENT = 0.30
    mstc = round(amount * MSTC_PERCENT, 2)
    musd = round(amount - mstc, 2)

    LEVEL_PERCENTS = [0.05, 0.03, 0.01]
    MAX_LEVELS = len(LEVEL_PERCENTS)

       db = SessionLocal()
    try:
        user = db.query(User).get(user_id)
        if not user:
            # Create user if not present (same logic as in /webapp/me)
            user = User(
                id=user_id,
                username=tg_user.get("username") or "",
                first_name=tg_user.get("first_name") or "",
                role="user",
                self_activated=False,
                balance_musd=0.0,
                balance_mstc=0.0,
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        # Was this user already Origin before this deposit?
        was_origin = bool(getattr(user, "self_activated", False))

        # Create Transaction records (match fields in backend/models.py)
        txn_musd = Transaction(
            user_id=user.id,
            amount=musd,
            currency="MUSD",
            type="deposit"
        )
        db.add(txn_musd)

        txn_mstc = Transaction(
            user_id=user.id,
            amount=mstc,
            currency="MSTC",
            type="credit_mstc"
        )
        db.add(txn_mstc)

        # credit user balances
        user.balance_musd = float(user.balance_musd or 0.0) + musd
        user.balance_mstc = float(user.balance_mstc or 0.0) + mstc
        db.add(user)

        # If user was not Origin before and this deposit qualifies, mark them Origin
        if not was_origin and amount >= MIN_DEPOSIT:
            user.self_activated = True
            try:
                if getattr(user, "role", "") != "origin":
                    user.role = "origin"
            except Exception:
                pass
            db.add(user)

            # Bump active_origin_count for all uplines once
            _increment_active_origins_for_upline(db, user)

        # credit upstream team business
        credit_team_business(db, user, amount)


        # referral chain & distribution
        chain = _get_referrer_chain(db, user, max_levels=MAX_LEVELS)
        referral_dist = []
        total_distributed = 0.0

        for level_idx, ref in enumerate(chain):
            base_pct = LEVEL_PERCENTS[level_idx] if level_idx < len(LEVEL_PERCENTS) else 0.0
            pct = base_pct
            if level_idx == 0 and is_life_changer(ref):
                pct = 0.10

            amount_for_ref = round(amount * pct, 2)
            if amount_for_ref <= 0:
                continue

            ref.balance_musd = float(ref.balance_musd or 0.0) + amount_for_ref
            db.add(ref)

            ref_evt = ReferralEvent(
                from_user=user.id,
                to_user=ref.id,
                amount=amount_for_ref,
                note=f"level_{level_idx+1}_referral",
            )
            db.add(ref_evt)

            referral_dist.append({
                "level": level_idx + 1,
                "to_user_id": int(ref.id),
                "to_username": getattr(ref, "username", None),
                "amount": amount_for_ref,
                "percent": pct,
            })
            total_distributed += amount_for_ref

        # leftover -> company_pool
        leftover = round(amount - total_distributed, 2)
        if leftover > 0:
            ref_evt = ReferralEvent(
                from_user=user.id,
                to_user=None,
                amount=leftover,
                note="company_pool_remainder",
            )
            db.add(ref_evt)
            referral_dist.append({
                "level": 0,
                "to_user_id": None,
                "to_username": "company_pool",
                "amount": leftover,
                "percent": None,
            })
            total_distributed += leftover

        db.commit()

        resp = {
            "ok": True,
            "mstc": mstc,
            "musd": musd,
            "referral_dist": referral_dist,
        }
        return jsonify(resp), 200

    except SQLAlchemyError as e:
        db.rollback()
        return jsonify({"ok": False, "error": "db_error", "detail": str(e)}), 500

    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        return jsonify({"ok": False, "error": "internal_error", "detail": str(e)}), 500

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
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("backend.app")
    logger.info("Starting backend.app entrypoint (pid=%s)", os.getpid())

    host = "127.0.0.1"
    port = 8001
    debug = True

    if len(sys.argv) >= 2 and sys.argv[1] == "run":
        if len(sys.argv) >= 3:
            try:
                port = int(sys.argv[2])
            except Exception:
                logger.warning("Invalid port passed, using default %s", port)
        logger.info("Flask run -> host=%s port=%s debug=%s", host, port, debug)
        app.run(host=host, port=port, debug=debug)
    else:
        print("Usage: python backend\\app.py run [port]")
        print("   or: python -m backend.app run [port]")
