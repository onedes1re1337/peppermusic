import hashlib
import hmac
import json
import time
from typing import Optional
from urllib.parse import parse_qsl

from config import BOT_TOKEN, DEV_MODE

# Фейковый пользователь для localhost
DEV_USER: dict = {
    "id": 999999999,
    "first_name": "Dev",
    "last_name": "User",
    "username": "dev_user",
    "language_code": "ru",
}


def validate_init_data(
    init_data: str,
    max_age: int = 86400,
) -> Optional[dict]:
    """
    Валидирует Telegram WebApp initData.
    В DEV_MODE при пустом / «dev» — возвращает DEV_USER.
    """
    # ── dev bypass ──
    if DEV_MODE and (not init_data or init_data == "dev"):
        return DEV_USER.copy()

    if not init_data:
        return None

    # ── parse ──
    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None

    received_hash = data.pop("hash", None)
    if not received_hash:
        return None

    # ── свежесть ──
    auth_date = int(data.get("auth_date", "0"))
    if auth_date and (time.time() - auth_date > max_age):
        return None

    # ── HMAC-SHA256 ──
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, received_hash):
        return None

    # ── user json ──
    try:
        user = json.loads(data.get("user", "{}"))
    except json.JSONDecodeError:
        return None

    return user if user.get("id") else None