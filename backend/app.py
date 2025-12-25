import os
import logging
import traceback
import json
from urllib.parse import parse_qsl
from datetime import datetime
from typing import Optional

from flask import Flask, request, jsonify, send_from_directory, current_app
from flask_cors import CORS
from sqlalchemy.exc import SQLAlchemyError
import requests
from dotenv import load_dotenv


# local imports
from backend.models import Base, engine, SessionLocal, User, Transaction, ReferralEvent, init_db

# -------------------------
# Load environment & logging
# -------------------------
# Load .env
load_dotenv()

# Configure logging FIRST
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Now it is safe to log
logger.info(
    "BOT_TOKEN loaded: %s",
    "YES" if os.getenv("BOT_TOKEN") else "NO"
)
# -------------------------
# Flask app creation
# -------------------------
app = Flask(__name__)
CORS(app)

# show only first 6 chars of DEBUG_KEY to confirm it's present (do not leak secret)
_debug_key = os.getenv("DEBUG_KEY") or app.config.get("DEBUG_KEY")
if _debug_key:
    app.logger.info("DEBUG_KEY present (first6): %s", str(_debug_key)[:6])
else:
    app.logger.info("DEBUG_KEY NOT present in environment.")

# safe DB url display (mask credentials if you must print)
try:
    db_url = str(engine.url)
    if "@" in db_url and ":" in db_url:
        parts = db_url.split("@", 1)
        visible = parts[1]
        app.logger.info("Flask DB URL (masked): %s", visible)
    else:
        app.logger.info("Flask DB URL: %s", db_url)
except Exception:
    app.logger.exception("Could not read engine.url")

# Initialize DB metadata (no destructive migrations)
# Initialize DB metadata (safe for Railway)
try:
    init_db()

    if os.getenv("RAILWAY_ENVIRONMENT"):
        app.logger.info("Skipping create_all on Railway")
    else:
        Base.metadata.create_all(bind=engine)
        app.logger.info("DB create_all executed (non-Railway)")

except Exception as e:
    app.logger.error("DB init failed, continuing without crash: %s", e)


app.logger.info("Flask CWD: %s", os.getcwd())
app.logger.info("Flask DB URL: %s", engine.url)

# -------------------------
# Helpers
# -------------------------

@app.route("/debug/routes", methods=["GET"])
def debug_routes():
    routes = []
    for r in app.url_map.iter_rules():
        routes.append({
            "rule": r.rule,
            "methods": sorted(list(r.methods)),
            "endpoint": r.endpoint
        })
    return jsonify(ok=True, routes=routes)

def check_debug_key():
    """
    Robust check for debug key. Accept header variants, query param 'debug_key' or 'key',
    and strip whitespace before comparing.
    """
    expected = current_app.config.get("DEBUG_KEY") or os.getenv("DEBUG_KEY")
    if not expected:
        current_app.logger.warning("check_debug_key: DEBUG_KEY not set in config or env")
        return False

    expected_norm = str(expected).strip()

    # try common header names
    for k in ("X-DEBUG-KEY", "X-Debug-Key", "x-debug-key"):
        val = request.headers.get(k)
        if val and str(val).strip() == expected_norm:
            return True

    # fallback: scan headers that contain both 'debug' and 'key'
    for hk, hv in request.headers.items():
        if "debug" in hk.lower() and "key" in hk.lower():
            if str(hv).strip() == expected_norm:
                return True

    # also accept query params for convenience
    for param in ("debug_key", "key"):
        q = request.args.get(param)
        if q and str(q).strip() == expected_norm:
            return True

    return False

def get_ref_from_payload(data: dict) -> Optional[int]:
    ref = data.get("ref")
    try:
        return int(ref) if ref is not None else None
    except (ValueError, TypeError):
        return None

def link_referrer_if_needed(db, user: User, maybe_referrer_id: int | None):
    if user.referrer_id is not None:
        return
    if not maybe_referrer_id:
        return
    if maybe_referrer_id == user.id:
        return
    ref = db.get(User, maybe_referrer_id)
    if not ref:
        return
    user.referrer_id = ref.id
    db.commit()
    db.refresh(user)

def create_user_only(db, tg_user, ref_id=None):
    """Create or update user.

    tg_user expected to be dict with keys: id, username, first_name, last_name(optional)
    """
    if not isinstance(tg_user, dict):
        raise ValueError(f"tg_user is not a dict: {tg_user!r}")

    tg_id = tg_user.get("id")
    if tg_id is None:
        raise ValueError("Telegram user id missing")

    username = tg_user.get("username")
    first_name = tg_user.get("first_name")
    last_name = tg_user.get("last_name")

    # Prefer to lookup by primary key if your app uses id as telegram id
    # Many parts of your code use User.id == telegram id; adjust if you use telegram_id column instead
    user = db.get(User, int(tg_id)) if hasattr(User, 'id') else None

    # Fallback: try telegram_id column
    if user is None:
        try:
            user = db.query(User).filter_by(telegram_id=str(tg_id)).first()
        except Exception:
            user = None

    if user is None:
        user = User(
            id=int(tg_id),
            telegram_id=int(tg_id) if hasattr(User, 'telegram_id') else None,
            username=username,
            first_name=first_name,
            last_name=last_name,
            created_at=datetime.utcnow(),
            balance_mstc=0.0,
            balance_musd=0.0,
            active=True,
            referrer_id=ref_id,
            role="user",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    # Existing user: update basic fields if changed
    changed = False
    if username and user.username != username:
        user.username = username
        changed = True
    if first_name and user.first_name != first_name:
        user.first_name = first_name
        changed = True
    if last_name and getattr(user, 'last_name', None) != last_name:
        user.last_name = last_name
        changed = True

    # Only set referrer if currently None
    if user.referrer_id is None and ref_id is not None:
        user.referrer_id = ref_id
        changed = True

    if changed:
        db.add(user)
        db.commit()
        db.refresh(user)

    return user

def get_uplines(db, user, max_levels=3):
    uplines = []
    current = user
    level = 1
    while level <= max_levels and getattr(current, 'referrer_id', None):
        upline = db.get(User, current.referrer_id)
        if not upline:
            break
        uplines.append((level, upline))
        current = upline
        level += 1
    return uplines

def verify_telegram_init_data(init_data: str):
    if not init_data:
        return None, None, None, None
    try:
        data = dict(parse_qsl(init_data, strict_parsing=True))
    except Exception:
        return None, None, None, None
    user_str = data.get("user")
    if not user_str:
        return None, None, None, None
    try:
        user = json.loads(user_str)
    except Exception:
        return None, None, None, None
    start_param = data.get("start_param")
    return user.get("id"), user.get("username"), user.get("first_name"), start_param

# -------------------------
# Business helpers
# -------------------------

def require_admin(user):
    return user and user.role in ("admin", "superadmin")

def update_rank(user: User):
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
    elif user.self_activated and user.role == "user":
        user.role = "origin"


ROLE_LEVEL1_PCT = {
    "origin": 0.05,
    "life_changer": 0.10,
    "advisor": 0.15,
    "visionary": 0.20,
    "creator": 0.25,
}

def propagate_team_business(db: SessionLocal, user: User, amount: float, became_origin_now: bool):
    visited = set()
    current = user
    while getattr(current, 'referrer_id', None) and current.referrer_id not in visited:
        ref = db.get(User, current.referrer_id)
        if not ref:
            break
        visited.add(ref.id)
        ref.total_team_business = (ref.total_team_business or 0.0) + amount
        if became_origin_now:
            ref.active_origin_count = (ref.active_origin_count or 0) + 1
        update_rank(ref)
        db.add(ref)
        current = ref

def distribute_club_bonus(db: SessionLocal, amount: float) -> float:
    club_cut = round(amount * 0.02, 2)
    if club_cut <= 0:
        return 0.0
    achievers = (
        db.query(User)
        .filter(
            User.self_activated == True,
            User.role.in_( ["life_changer", "advisor", "visionary", "creator"] )
        )
        .all()
    )
    if not achievers:
        add_to_company_pool(db, club_cut)
        return club_cut
    per_user = round(club_cut / len(achievers), 2)
    if per_user <= 0:
        add_to_company_pool(db, club_cut)
        return club_cut
    distributed_total = 0.0
    for u in achievers:
        u.club_income = float(u.club_income or 0.0) + per_user
        db.add(u)
        distributed_total += per_user
    leftover = round(club_cut - distributed_total, 2)
    if leftover > 0:
        add_to_company_pool(db, leftover)
    return club_cut

COMPANY_USER_ID = -999999999

def get_company_user(db: SessionLocal) -> User:
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
    amount = float(amount or 0.0)
    if amount <= 0:
        return
    company = get_company_user(db)
    company.balance_musd = float(company.balance_musd or 0.0) + amount
    db.add(company)
    if commit:
        db.commit()
        db.refresh(company)

# -------------------------
# Routes
# -------------------------

DEPOSIT_API_KEY = os.getenv("DEPOSIT_API_KEY")

@app.route("/", methods=["GET"])
def home():
    return "Backend OK", 200

@app.route("/webapp/me", methods=["POST"])
def webapp_me():
    db = SessionLocal()
    try:
        payload = request.get_json(silent=True) or {}
        init_data = payload.get("initData")

        telegram_id, _, _, _ = verify_telegram_init_data(init_data)
        if not telegram_id:
            return jsonify({"ok": False}), 400

        user = db.query(User).filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            return jsonify({"ok": False, "not_registered": True})

        return jsonify({
            "ok": True,
            "user": {
                "id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "role": user.role,
                "balance_mstc": user.balance_mstc,
                "balance_musd": user.balance_musd,
                "referrer_id": user.referrer_id,
            }
        })
    finally:
        db.close()

@app.route("/webapp/init", methods=["POST"])
def webapp_init():
    db = SessionLocal()
    try:
        data = request.get_json(silent=True) or {}
        init_data = data.get("initData")

        telegram_id, username, first_name, start_param = verify_telegram_init_data(init_data)
        if not telegram_id:
            return jsonify({"ok": False}), 400

        # 🔍 ONLY CHECK USER
        user = db.query(User).filter_by(telegram_id=str(telegram_id)).first()

        # 🔍 Resolve referrer ONLY FOR DISPLAY
        referrer_username = None
        if start_param:
            ref_user = db.query(User).filter_by(telegram_id=str(start_param)).first()
            if ref_user:
                referrer_username = ref_user.username or ref_user.first_name

        return jsonify({
            "ok": True,
            "exists": bool(user),
            "referrer_username": referrer_username
        })

    finally:
        db.close()

@app.route("/webapp/register", methods=["POST"])
def webapp_register():
    db = SessionLocal()
    try:
        payload = request.get_json(silent=True) or {}
        init_data = payload.get("initData")

        telegram_id, username, first_name, start_param = verify_telegram_init_data(init_data)
        if not telegram_id:
            return jsonify({"ok": False}), 400

        # 🛑 Prevent auto / double registration
        existing = db.query(User).filter_by(telegram_id=str(telegram_id)).first()
        if existing:
            return jsonify({"ok": True, "already": True})

        referrer_id = None
        if start_param:
            ref_user = db.query(User).filter_by(telegram_id=str(start_param)).first()
            if ref_user:
                referrer_id = ref_user.id

        user = User(
            telegram_id=str(telegram_id),
            username=username,
            first_name=first_name,
            referrer_id=referrer_id
        )

        db.add(user)
        db.commit()

        return jsonify({"ok": True})

    finally:
        db.close()

@app.route("/webapp/user", methods=["POST"])
def webapp_user():
    db = SessionLocal()
    try:
        data = request.get_json() or {}
        init_data = data.get("initData")

        telegram_id, _, _, _ = verify_telegram_init_data(init_data)
        if not telegram_id:
            return jsonify({"ok": False}), 400

        user = db.query(User).filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            return jsonify({"ok": False, "error": "user_not_found"}), 404

        # ✅ ADMIN CHECK VIA ENV
        admin_ids = os.getenv("ADMIN_TELEGRAM_IDS", "")
        admin_set = {
            int(x.strip()) for x in admin_ids.split(",") if x.strip().isdigit()
        }

        is_admin = int(telegram_id) in admin_set

        return jsonify({
            "ok": True,
            "user": {
                "id": user.id,
                "role": user.role,
                "self_activated": bool(user.self_activated),
                "total_team_business": float(user.total_team_business or 0),
                "active_origin_count": int(user.active_origin_count or 0),
                "username": user.username,
                "first_name": user.first_name,
                "is_admin": is_admin
            }
        })
    finally:
        db.close()

@app.route("/admin/users", methods=["POST"])
def admin_users():
    db = SessionLocal()
    try:
        data = request.get_json() or {}
        init_data = data.get("initData")

        if not init_data:
            return jsonify({"ok": False, "error": "missing_init_data"}), 400

        uid, _, _, _ = verify_telegram_init_data(init_data)
        if not uid:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

        user = db.query(User).filter(User.id == uid).first()
        if not require_admin(user):
         return jsonify({"ok": False, "error": "forbidden"}), 403
        users = (
            db.query(User)
            .order_by(User.created_at.desc())
            .limit(50)
            .all()
        )

        return jsonify({
            "ok": True,
            "users": [
                {
                    "id": u.id,
                    "username": u.username,
                    "first_name": u.first_name,
                    "role": u.role,
                    "balance_musd": float(u.balance_musd),
                    "balance_mstc": float(u.balance_mstc),
                    "active": u.active
                }
                for u in users
            ]
        })
    except Exception as e:
        logger.exception("admin_users failed")
        return jsonify({"ok": False, "error": "server_error"}), 500
    finally:
        db.close()    

@app.route("/admin/update_user", methods=["POST"])
def admin_update_user():
    db = SessionLocal()
    try:
        data = request.get_json() or {}
        init_data = data.get("initData")
        target_id = data.get("user_id")
        action = data.get("action")

        if not init_data or not target_id or not action:
            return jsonify({"ok": False, "error": "missing_params"}), 400

        admin_id, _, _, _ = verify_telegram_init_data(init_data)
        admin = db.query(User).filter(User.id == admin_id).first()

        if not admin or admin.role not in ("admin", "superadmin"):
            return jsonify({"ok": False, "error": "forbidden"}), 403

        user = db.query(User).filter(User.id == target_id).first()
        if not user:
            return jsonify({"ok": False, "error": "user_not_found"}), 404

        # ---- ACTIONS ----
        if action == "promote":
            user.role = "admin"
        elif action == "demote":
            user.role = "user"
        elif action == "activate":
            user.active = True
        elif action == "deactivate":
            user.active = False
        else:
            return jsonify({"ok": False, "error": "invalid_action"}), 400

        db.commit()

        return jsonify({
            "ok": True,
            "user": {
                "id": user.id,
                "role": user.role,
                "active": user.active
            }
        })

    except Exception:
        logger.exception("admin_update_user failed")
        return jsonify({"ok": False, "error": "server_error"}), 500
    finally:
        db.close()

@app.route("/admin/impersonate", methods=["POST"])
def admin_impersonate():
    db = SessionLocal()
    try:
        data = request.get_json() or {}
        init_data = data.get("initData")
        target_id = data.get("user_id")

        if not init_data or not target_id:
            return jsonify({"ok": False}), 400

        admin_id, _, _, _ = verify_telegram_init_data(init_data)
        admin = db.query(User).filter(User.id == admin_id).first()

        if not admin or admin.role not in ("admin", "superadmin"):
            return jsonify({"ok": False, "error": "forbidden"}), 403

        target = db.query(User).filter(User.id == target_id).first()
        if not target or target.role in ("admin", "superadmin"):
            return jsonify({"ok": False, "error": "cannot_impersonate"}), 400

        return jsonify({
            "ok": True,
            "impersonated_user": {
                "id": target.id,
                "first_name": target.first_name,
                "username": target.username,
                "role": target.role
            }
        })

    except Exception:
        logger.exception("admin_impersonate failed")
        return jsonify({"ok": False}), 500
    finally:
        db.close()

@app.route("/admin/stats", methods=["POST"])
def admin_stats():
    db = SessionLocal()
    try:
        data = request.get_json() or {}
        init_data = data.get("initData")

        if not init_data:
            return jsonify({"ok": False, "error": "missing_init_data"}), 400

        uid, _, _, _ = verify_telegram_init_data(init_data)
        if not uid:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

        admin = db.query(User).get(uid)
        if not require_admin(admin):
            return jsonify({"ok": False, "error": "forbidden"}), 403

        # --------- STATS ----------
        total_users = db.query(User).count()
        active_users = db.query(User).filter(User.active == True).count()
        admin_count = db.query(User).filter(User.role.in_(("admin", "superadmin"))).count()

        total_team_business = (
            db.query(func.coalesce(func.sum(User.total_team_business), 0))
            .scalar()
        )

        total_musd_balance = (
            db.query(func.coalesce(func.sum(User.balance_musd), 0))
            .scalar()
        )

        today = datetime.utcnow().date()
        today_deposits = (
            db.query(func.coalesce(func.sum(Transaction.amount), 0))
            .filter(func.date(Transaction.created_at) == today)
            .scalar()
        )

        return jsonify({
            "ok": True,
            "stats": {
                "total_users": total_users,
                "active_users": active_users,
                "admin_count": admin_count,
                "total_team_business": float(total_team_business),
                "total_musd_balance": float(total_musd_balance),
                "today_deposits": float(today_deposits),
            }
        })
    except Exception:
        logger.exception("admin_stats failed")
        return jsonify({"ok": False, "error": "server_error"}), 500
    finally:
        db.close()

@app.route("/webapp/save_wallet", methods=["POST"])
def save_wallet():
    db = SessionLocal()
    try:
        data = request.get_json()
        init_data = data.get("initData")
        ton_wallet = data.get("ton_wallet")

        telegram_id, _, _, _ = verify_telegram_init_data(init_data)
        if not telegram_id:
            return jsonify({"ok": False, "error": "invalid_init_data"}), 400

        user = db.query(User).filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            return jsonify({"ok": False, "error": "user_not_found"}), 404

        user.ton_wallet = ton_wallet
        db.commit()

        return jsonify({"ok": True, "ton_wallet": ton_wallet})

    except Exception:
        app.logger.exception("save_wallet error")
        return jsonify({"ok": False, "error": "server_error"}), 500
    finally:
        db.close()

@app.post("/bot/start")
def bot_start():
    data = request.get_json(silent=True) or {}
    tg_id = data.get("telegram_id")
    username = data.get("username")
    first_name = data.get("first_name")
    ref_code = data.get("ref_code")
    if not tg_id:
        return jsonify({"ok": False, "error": "missing_telegram_id"}), 400
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(telegram_id=str(tg_id)).first()
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
            if ref_code:
                try:
                    user.referrer_id = int(ref_code)
                except Exception:
                    pass
            db.add(user)
            changed = True
        else:
            if ref_code and not getattr(user, "referrer_id", None):
                try:
                    user.referrer_id = int(ref_code)
                    changed = True
                except Exception:
                    pass
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
        webapp_url = f"{os.getenv('BASE_URL', 'https://mstcbotnew-production.up.railway.app')}/static/telegram_mini_app.html"
        return jsonify({
            "ok": True,
            "message": message,
            "button_label": button_label,
            "webapp_url": webapp_url,
        })
    finally:
        db.close()

@app.route("/webapp/profile", methods=["POST"])
def webapp_profile():
    db = SessionLocal()
    try:
        data = request.get_json() or {}
        init_data = data.get("initData")

        uid, _, _, _ = verify_telegram_init_data(init_data)
        if not uid:
            return jsonify({"ok": False}), 401

        user = db.query(User).filter(User.id == uid).first()
        if not user:
            return jsonify({"ok": False}), 404

        return jsonify({
            "ok": True,
            "user": {
                "id": user.id,
                "first_name": user.first_name,
                "username": user.username,
                "role": user.role,
                "balance_mstc": float(user.balance_mstc),
                "balance_musd": float(user.balance_musd),
                "total_team_business": float(user.total_team_business),
                "active_origin_count": user.active_origin_count
            }
        })
    finally:
        db.close()

@app.route("/webapp/downlines", methods=["POST"])
def webapp_downlines():
    db = SessionLocal()
    try:
        data = request.get_json() or {}
        init_data = data.get("initData")

        uid, _, _, _ = verify_telegram_init_data(init_data)
        if not uid:
            return jsonify({"ok": False}), 401

        downlines = db.query(User).filter(User.referrer_id == uid).all()

        return jsonify({
            "ok": True,
            "downlines": [
                {
                    "id": u.id,
                    "first_name": u.first_name,
                    "username": u.username,
                    "role": u.role,
                    "team_business": float(u.total_team_business)
                } for u in downlines
            ]
        })
    finally:
        db.close()

@app.route("/webapp/role", methods=["POST"])
def webapp_role():
    db = SessionLocal()
    try:
        data = request.get_json() or {}
        init_data = data.get("initData")

        uid, _, _, _ = verify_telegram_init_data(init_data)
        if not uid:
            return jsonify({"ok": False}), 401

        user = db.query(User).filter(User.id == uid).first()
        if not user:
            return jsonify({"ok": False}), 404

        return jsonify({
            "ok": True,
            "role": user.role,
            "active_origin_count": user.active_origin_count,
            "total_team_business": float(user.total_team_business)
        })
    finally:
        db.close()

# -------------------------
# Debug / admin endpoints
# -------------------------

@app.route("/debug/downlines/<int:user_id>")
def debug_downlines(user_id):
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            return jsonify({"exists": False, "error": "user_not_found"})
        direct = db.query(User).filter(User.referrer_id == user_id).all()
        return jsonify({
            "exists": True,
            "user": {"id": user.id, "first_name": user.first_name, "username": user.username, "role": user.role, "self_activated": user.self_activated, "referrer_id": user.referrer_id, "total_team_business": float(user.total_team_business or 0)},
            "direct_downlines": [
                {"id": d.id, "first_name": d.first_name, "username": d.username, "role": d.role, "self_activated": d.self_activated, "referrer_id": d.referrer_id, "total_team_business": float(d.total_team_business or 0)}
                for d in direct
            ],
            "direct_downline_count": len(direct),
        })
    finally:
        db.close()

@app.route("/debug/link_referrer", methods=["POST"])
def debug_link_referrer():
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
        app.logger.exception("Error in /debug/link_referrer: %s", e)
        return jsonify(ok=False, error="db_error", detail=str(e)), 500
    finally:
        db.close()

@app.route("/debug/list_users", methods=["GET"])
def debug_list_users():
    db = SessionLocal()
    try:
        users = db.query(User).all()
        data = [{"id": u.id, "username": u.username, "first_name": u.first_name, "self_activated": u.self_activated, "referrer_id": u.referrer_id, "total_team_business": u.total_team_business, "active_origin_count": u.active_origin_count, "role": u.role} for u in users]
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
        return jsonify(ok=True, exists=True, user_id=company.id, username=company.username, role=company.role, balance_musd=float(company.balance_musd or 0.0), balance_mstc=float(company.balance_mstc or 0.0), club_income=float(company.club_income or 0.0) if hasattr(company, "club_income") else 0.0)
    finally:
        db.close()

# Single, canonical debug simulate_deposit implementation
@app.route("/debug/simulate_deposit", methods=["POST"])
def debug_simulate_deposit():
    # -------- DEBUG KEY --------
    if not check_debug_key():
        return jsonify(ok=False, error="invalid_debug_key"), 401

    # -------- INPUT --------
    payload = request.get_json(silent=True) or {}
    tg_id = payload.get("user_id")
    amount = payload.get("amount")
    tx_musd = payload.get("tx_musd")

    try:
        tg_id = int(tg_id)
        amount = float(amount)
    except Exception:
        return jsonify(ok=False, error="missing_user_or_amount"), 400

    db = SessionLocal()
    try:
        # -------- USER --------
        user = db.query(User).filter_by(telegram_id=str(tg_id)).first()
        if not user:
         return jsonify(ok=False, error="user_not_found"), 404

        # -------- ACTIVATE & ROLE --------
        became_origin_now = False

        if amount >= 20:
            if not user.self_activated:
                user.self_activated = True

            if user.role not in ("origin", "life_changer", "advisor", "visionary", "creator", "admin", "superadmin"):
                user.role = "origin"
                became_origin_now = True

        # --------       USER BUSINESS --------
        user.total_team_business = (user.total_team_business or 0.0) + amount
        db.add(user)

        # 🔥 propagate team business & ranks
        propagate_team_business(db, user, amount, became_origin_now)
        update_rank(user)

        # -------- TRANSACTION --------
        db.add(Transaction(
            user_id=user.id,
            amount=amount,
            currency="MUSD",
            type="deposit",
            external_id=str(tx_musd),
            created_at=datetime.utcnow()
        ))

        db.commit()
        db.refresh(user)

        return jsonify(
            ok=True,
            became_origin_now=became_origin_now,
            user={
                "id": user.id,
                "role": user.role,
                "self_activated": user.self_activated,
                "total_team_business": user.total_team_business
            }
        ), 200

    except Exception:
        db.rollback()
        app.logger.exception("debug_simulate_deposit failed")
        return jsonify(ok=False, error="server_error"), 500

    finally:
        db.close()


 
@app.route("/debug/user/<int:user_id>")
def debug_user(user_id):
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            return jsonify({"exists": False})
        return jsonify({"exists": True, "id": user.id, "username": user.username, "first_name": user.first_name, "self_activated": user.self_activated, "role": user.role, "referrer_id": user.referrer_id, "total_team_business": float(user.total_team_business or 0)})
    finally:
        db.close()

@app.route("/debug/reset_user/<int:user_id>", methods=["POST"])
def debug_reset_user(user_id):
    if not check_debug_key():
        return jsonify(ok=False, error="invalid_debug_key"), 401

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return jsonify(ok=False, error="user_not_found"), 404

        # 🔥 delete referral events (MATCH NEW MODEL)
        db.query(ReferralEvent).filter(
    (ReferralEvent.from_user == user.id) |
    (ReferralEvent.to_user == user.id)
).delete(synchronize_session=False)

        # 🔥 delete transactions
        db.query(Transaction).filter(
            Transaction.user_id == user_id
        ).delete(synchronize_session=False)

        # 🔥 reset user fields
        user.balance_musd = 0
        user.balance_mstc = 0
        user.total_team_business = 0
        user.active_origin_count = 0
        user.self_activated = False
        user.referrer_id = None
        user.role = "user"

        db.commit()

        return jsonify(ok=True, user_id=user_id)

    except Exception as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), 500
    finally:
        db.close()


@app.route("/debug/transactions/<int:user_id>", methods=["GET"])
def debug_transactions(user_id):
    db = SessionLocal()
    try:
        txs = (
            db.query(Transaction)
            .filter_by(user_id=user_id)
            .order_by(Transaction.created_at.desc())
            .all()
        )
        out = []
        for t in txs:
            out.append({
                "id": getattr(t, "id", None),
                "user_id": t.user_id,
                "amount": float(t.amount or 0.0),
                "currency": t.currency,
                "type": t.type,
                "external_id": t.external_id,
                "created_at": t.created_at.isoformat() if getattr(t, "created_at", None) else None
            })
        return jsonify(ok=True, transactions=out)
    finally:
        db.close()

 
@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True)
    app.logger.info("Webhook update: %s", update)

    if not update:
        return jsonify(ok=True)

    from telegram_bot import handle_command
    handle_command(update)

    return jsonify(ok=True)


# Entry point for local run
if __name__ == "__main__":
    logger.info("Starting backend.app entrypoint (pid=%s)", os.getpid())
    port = int(os.environ.get("PORT", 8001))
    host = "0.0.0.0"
    debug = False
    logger.info("Flask run -> host=%s port=%s debug=%s", host, port, debug)
    app.run(host=host, port=port, debug=debug)


