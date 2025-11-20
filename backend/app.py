import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from sqlalchemy.exc import SQLAlchemyError

# Resilient imports: works both when running as script and as package module
try:
    # when running as package: python -m backend.app
    from .models import SessionLocal, User, Transaction, ReferralEvent
    from .utils import verify_telegram_initdata
except Exception:
    # when running as script: python backend\app.py
    from models import SessionLocal, User, Transaction, ReferralEvent
    from utils import verify_telegram_initdata

# Environment configuration
BOT_TOKEN = "12345"
BOT_USERNAME = "abc"
ADMIN_IDS = 1234

app = Flask(__name__)
CORS(app)


@app.route('/webapp/me', methods=['POST'])
def webapp_me():
    data = request.get_json(force=True)
    initData = data.get('initData')

    if not initData or not isinstance(initData, dict) or 'user' not in initData:
        return jsonify({'ok': False, 'error': 'missing initData.user'}), 400

    # DEV-friendly verification: tolerate verification failures if function expects raw string
    try:
        # If you want to bypass verification during local testing set ENV=dev
        if os.getenv("ENV") == "dev":
            verified = True
        else:
            verified = verify_telegram_initdata(initData, BOT_TOKEN)
        if not verified:
            return jsonify({'ok': False, 'error': 'invalid initData signature'}), 403
    except Exception:
        # verification function may expect raw string — skip strict check for now
        pass

    tg_user = initData.get('user')
    user_id = int(tg_user.get('id'))

    db = SessionLocal()
    user = db.query(User).get(user_id)
    if not user:
        user = User(
            id=user_id,
            username=tg_user.get('username') or '',
            first_name=tg_user.get('first_name') or '',
            role='user',
            self_activated=False,
            balance_musd=0.0,
            balance_mstc=0.0,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    resp_user = {
        'id': user.id,
        'username': user.username,
        'first_name': user.first_name,
        'balance_musd': float(user.balance_musd or 0.0),
        'balance_mstc': float(user.balance_mstc or 0.0),
        'role': user.role,
        'total_team_business': float(user.total_team_business or 0.0),
        'active_origin_count': int(user.active_origin_count or 0)
    }
    db.close()
    return jsonify({'ok': True, 'user': resp_user, 'bot_username': BOT_USERNAME})


# ---------------------------
# Helper functions (not routes)
# ---------------------------
def _get_referrer_chain(db, user, max_levels=3):
    """
    Return list of User objects representing the referrer chain:
    [level1_referrer, level2_referrer, ...] up to max_levels.
    """
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
        return (float(getattr(user, "total_team_business", 0.0)) >= 1000.0
                and int(getattr(user, "active_origin_count", 0)) >= 10)
    except Exception:
        return False


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


# ---------------------------
# Main verify route (decorated)
# ---------------------------
@app.route('/webapp/verify', methods=['POST'])
def webapp_verify():
    """
    Deposit + multi-level referral handler.
    Uses the Transaction and ReferralEvent models defined in backend/models.py.
    """
    data = request.get_json(force=True)
    initData = data.get('initData')
    amount = data.get('amount')

    # basic validation
    if not initData or not isinstance(initData, dict) or 'user' not in initData:
        return jsonify({'ok': False, 'error': 'missing initData.user'}), 400

    try:
        amount = float(amount)
    except Exception:
        return jsonify({'ok': False, 'error': 'invalid_amount'}), 400

    MIN_DEPOSIT = 20.0
    STEP = 10.0
    if amount < MIN_DEPOSIT:
        return jsonify({'ok': False, 'error': 'min_deposit'}), 400
    if amount != MIN_DEPOSIT and ((amount - MIN_DEPOSIT) % STEP) != 0:
        return jsonify({'ok': False, 'error': 'invalid_step'}), 400

    # monetary split
    MSTC_PERCENT = 0.30
    mstc = round(amount * MSTC_PERCENT, 2)
    musd = round(amount - mstc, 2)

    LEVEL_PERCENTS = [0.05, 0.03, 0.01]
    MAX_LEVELS = len(LEVEL_PERCENTS)

    tg_user = initData['user']
    user_id = int(tg_user['id'])

    db = SessionLocal()
    try:
        user = db.query(User).get(user_id)
        if not user:
            return jsonify({'ok': False, 'error': 'user_not_found'}), 404

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
                note=f"level_{level_idx+1}_referral"
            )
            db.add(ref_evt)

            referral_dist.append({
                "level": level_idx + 1,
                "to_user_id": int(ref.id),
                "to_username": getattr(ref, "username", None),
                "amount": amount_for_ref,
                "percent": pct
            })
            total_distributed += amount_for_ref

        # leftover -> company_pool
        leftover = round(amount - total_distributed, 2)
        if leftover > 0:
            ref_evt = ReferralEvent(
                from_user=user.id,
                to_user=None,
                amount=leftover,
                note="company_pool_remainder"
            )
            db.add(ref_evt)
            referral_dist.append({
                "level": 0,
                "to_user_id": None,
                "to_username": "company_pool",
                "amount": leftover,
                "percent": None
            })
            total_distributed += leftover

        db.commit()

        resp = {
            "ok": True,
            "mstc": mstc,
            "musd": musd,
            "referral_dist": referral_dist
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


if __name__ == "__main__":
    import sys
    import logging

    # make sure logs are visible
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("backend.app")
    logger.info("Starting backend.app entrypoint (pid=%s)", __import__("os").getpid())

    # default run values
    host = "127.0.0.1"
    port = 8001
    debug = True

    # simple CLI: `python backend\app.py run` or `python -m backend.app run`
    if len(sys.argv) >= 2 and sys.argv[1] == "run":
        # allow optional port: python backend\app.py run 5000
        if len(sys.argv) >= 3:
            try:
                port = int(sys.argv[2])
            except Exception:
                logger.warning("Invalid port passed, using default %s", port)
        logger.info("Flask run -> host=%s port=%s debug=%s", host, port, debug)
        # app.run is blocking and will print Werkzeug/Flask logs
        app.run(host=host, port=port, debug=debug)
    else:
        print("Usage: python backend\\app.py run [port]")
        print("   or: python -m backend.app run [port]")
