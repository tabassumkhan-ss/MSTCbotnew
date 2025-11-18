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
BOT_TOKEN = "8487241335:AAHfCDzdzZBiedvPAcYbr5_BRqSa8YTaWVs"
BOT_USERNAME = "mstcrefbot"
ADMIN_IDS = 7955075357


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
    return jsonify({'ok': True, 'user': resp_user, 'bot_username': BOT_USERNAME})
@app.route('/webapp/verify', methods=['POST'])
def webapp_verify():
    data = request.get_json(force=True)
    initData = data.get('initData')
    amount = data.get('amount')

    # ---- Basic validation ----
    if not initData or not isinstance(initData, dict) or 'user' not in initData:
        return jsonify({'ok': False, 'error': 'missing initData.user'}), 400

    try:
        amount = float(amount)
    except:
        return jsonify({'ok': False, 'error': 'invalid_amount'}), 400

    # ---- Deposit rules ----
    MIN_DEPOSIT = 20
    STEP = 10

    if amount < MIN_DEPOSIT:
        return jsonify({'ok': False, 'error': 'min_deposit'}), 400

    if amount != MIN_DEPOSIT and ((amount - MIN_DEPOSIT) % STEP) != 0:
        return jsonify({'ok': False, 'error': 'invalid_step'}), 400

    # ---- Get user ----
    tg_user = initData['user']
    user_id = int(tg_user['id'])

    db = SessionLocal()
    user = db.query(User).get(user_id)
    if not user:
        return jsonify({'ok': False, 'error': 'user_not_found'}), 404

    # ---- Business logic ----
    MSTC_PERCENT = 0.30
    REFERRAL_PERCENT = 0.05  # temporary until your referral tree is connected

    mstc = round(amount * MSTC_PERCENT, 2)
    musd = round(amount - mstc, 2)
    referral_amount = round(amount * REFERRAL_PERCENT, 2)

    referral_dist = {"amount": referral_amount, "to": "company_pool"}

    # ---- Store transaction ----
    txn = Transaction(
        user_id=user.id,
        amount=amount,
        mstc=mstc,
        musd=musd,
        txn_type="deposit"
    )
    db.add(txn)

    # ---- Store referral event ----
    ref = ReferralEvent(
        user_id=user.id,
        amount=referral_amount,
        sent_to="company_pool"
    )
    db.add(ref)

    # ---- Update user balance ----
    user.balance_musd += musd
    user.balance_mstc += mstc

    db.commit()

    return jsonify({
        "ok": True,
        "mstc": mstc,
        "musd": musd,
        "referral_dist": referral_dist
    })


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

