"""
api/auth.py — проверка Telegram Mini App initData

Telegram передаёт initData в заголовке X-Telegram-Init-Data.
Мы проверяем подпись через HMAC-SHA256 и извлекаем user.id.

Документация: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

import hashlib
import hmac
import json
from urllib.parse import parse_qsl, unquote

from fastapi import Header, HTTPException, Depends
from utils.config import BOT_TOKEN
from utils.db.jury import is_verified, is_admin as _is_admin


def _verify_init_data(init_data: str) -> dict:
    """
    Проверяет подпись initData и возвращает данные пользователя.
    Выбрасывает HTTPException если подпись невалидна.
    """
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid initData format")

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Missing hash in initData")

    # Строка для проверки: все поля кроме hash, отсортированные по ключу
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed.items())
    )

    # Секретный ключ: HMAC-SHA256 от токена бота с ключом "WebAppData"
    secret_key = hmac.new(
        b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256
    ).digest()

    expected_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        raise HTTPException(status_code=401, detail="Invalid initData signature")

    # Извлекаем данные пользователя
    user_str = parsed.get("user")
    if not user_str:
        raise HTTPException(status_code=401, detail="No user in initData")

    try:
        return json.loads(unquote(user_str))
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid user data")


def get_current_user(
    x_telegram_init_data: str = Header(default="", alias="X-Telegram-Init-Data")
) -> dict:
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Откройте через Telegram (initData отсутствует)")
    """
    Dependency: извлекает и проверяет пользователя из initData.
    Возвращает dict с полями id, first_name, username и tg_id (str).
    """
    user = _verify_init_data(x_telegram_init_data)
    user["tg_id"] = str(user["id"])
    return user


def get_verified_user(user: dict = Depends(get_current_user)) -> dict:
    """
    Dependency: проверяет что пользователь верифицирован.
    """
    if not is_verified(user["tg_id"]):
        raise HTTPException(status_code=403, detail="Not verified")
    return user


def get_admin_user(user: dict = Depends(get_current_user)) -> dict:
    """
    Dependency: проверяет что пользователь является администратором.
    """
    if not _is_admin(user["tg_id"]):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
