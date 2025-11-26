import os
import json
import hashlib
import hmac
import requests

# Base URL of your backend API
# You can also set this in .env as BACKEND_URL, otherwise it uses localhost:8001
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8001")

# Admin IDs (Telegram user IDs), optional.
# You can set in .env as: ADMIN_IDS=123456,789012
_admin_ids_env = os.getenv("ADMIN_IDS", "")
if _admin_ids_env.strip():
    ADMIN_IDS = {int(x) for x in _admin_ids_env.split(",") if x.strip().isdigit()}
else:
    ADMIN_IDS = set()


def is_admin(user_id: int) -> bool:
    """
    Return True if this Telegram user_id is in the admin list.
    """
    try:
        return int(user_id) in ADMIN_IDS
    except Exception:
        return False


def call_backend(path: str, method: str = "GET", json: dict | None = None):
    """
    Helper to call your Flask backend.

    path: e.g. "/admin/stats" or "/cron/payout"
    method: "GET" or "POST"
    json: JSON body for POST
    """
    url = BACKEND_URL.rstrip("/") + path
    try:
        resp = requests.request(method=method, url=url, json=json, timeout=10)
        return resp
    except Exception as e:
        # You can log or print here if you like
        print(f"call_backend error for {url}: {e}")
        return None


def verify_telegram_initdata(init_data: dict, bot_token: str) -> bool:
    """
    Verify Telegram WebApp initData server-side.
    init_data: parsed dict containing the fields of initData (must include "hash")
    bot_token: your bot's token (string)
    """
    if not init_data or 'hash' not in init_data:
        return False

    # Telegram's "hash" sent to us
    received_hash = init_data['hash']

    # Step 1: build data_check_string (exclude 'hash' itself)
    data_check_items = []
    for key in sorted(k for k in init_data.keys() if k != 'hash'):
        value = init_data[key]

        # Telegram requires nested objects (e.g. 'user') to be JSON serialized
        if isinstance(value, dict):
            value = json.dumps(value, separators=(',', ':'), ensure_ascii=False)

        data_check_items.append(f"{key}={value}")

    data_check_string = "\n".join(data_check_items)

    # Step 2: HMAC secret key is SHA256(bot_token)
    secret_key = hashlib.sha256(bot_token.encode()).digest()

    # Step 3: compute HMAC of data_check_string
    computed_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256
    ).hexdigest()

    # Step 4: compare securely
    return hmac.compare_digest(computed_hash, received_hash)
