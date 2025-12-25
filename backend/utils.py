import hashlib
import hmac
from urllib.parse import parse_qsl

def verify_telegram_initdata(init_data: str, bot_token: str) -> bool:
    """
    Validates Telegram WebApp initData using Telegram's HMAC-SHA256 method.
    """
    if not init_data:
        return False

    try:
        # Split into dict
        data = dict(parse_qsl(init_data, keep_blank_values=True))

        if "hash" not in data:
            return False

        received_hash = data.pop("hash")

        # Build data_check_string sorted lexicographically
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))

        secret_key = hashlib.sha256(bot_token.encode()).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        return calculated_hash == received_hash

    except Exception:
        return False
    
def is_admin(telegram_id: int) -> bool:
    """
    Check if telegram_id is in ADMIN_TELEGRAM_IDS env variable
    """
    admin_ids = os.getenv("ADMIN_TELEGRAM_IDS", "")

    try:
        admin_set = {
            int(x.strip())
            for x in admin_ids.split(",")
            if x.strip().isdigit()
        }
    except Exception:
        admin_set = set()

    return int(telegram_id) in admin_set