# backend/utils.py

import os
import requests
import logging

logger = logging.getLogger(__name__)

# -------------------------
# Backend HTTP helper
# -------------------------
def call_backend(path, method="GET", json=None, headers=None):
    """
    Call backend API from Telegram bot
    """
    base_url = os.getenv(
        "BASE_URL",
        "https://mstcbotnew-production.up.railway.app"
    )

    url = base_url.rstrip("/") + path

    try:
        resp = requests.request(
            method=method,
            url=url,
            json=json,
            headers=headers,
            timeout=10
        )
        return resp
    except Exception as e:
        logger.exception("call_backend failed: %s", e)
        return None


# -------------------------
# Admin helper
# -------------------------
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
