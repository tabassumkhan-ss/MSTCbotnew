# backend/utils.py
import hashlib
import hmac
import json


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
