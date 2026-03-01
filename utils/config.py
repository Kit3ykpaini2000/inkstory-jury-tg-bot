"""
utils/config.py — все настройки приложения из .env

Все параметры читаются при первом импорте.
Если обязательный параметр отсутствует — сразу ValueError при старте.
"""

import os
import pathlib
from dotenv import load_dotenv

load_dotenv(pathlib.Path(__file__).parent.parent / ".env")


def _int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def _float(key: str, default: float) -> float:
    return float(os.getenv(key, str(default)))


def _str(key: str, default: str) -> str:
    return os.getenv(key, default)


# ── Telegram ──────────────────────────────────────────────────────────────────

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

# ── Парсер ────────────────────────────────────────────────────────────────────

PARSER_INTERVAL: int   = _int("PARSER_INTERVAL", 30) * 60   # минуты → секунды
PAGE_SIZE: int         = _int("PAGE_SIZE", 20)               # постов на страницу API
PAGE_PAUSE_LINKS: float = _float("PAGE_PAUSE_LINKS", 1.0)   # пауза между запросами links (сек)
PAGE_PAUSE_POSTS: float = _float("PAGE_PAUSE_POSTS", 1.5)   # пауза между запросами posts (сек)

# ── Очередь ───────────────────────────────────────────────────────────────────

QUEUE_MODE: str        = _str("QUEUE_MODE", "distributed")   # open | distributed | balanced
EXPIRE_MINUTES: int    = _int("EXPIRE_MINUTES", 30)          # минут до освобождения поста
EXPIRE_CHECK_INTERVAL: int = _int("EXPIRE_CHECK_INTERVAL", 5) * 60  # минуты → секунды

# ── Расписание ────────────────────────────────────────────────────────────────

FINAL_PARSER_HOUR: int   = _int("FINAL_PARSER_HOUR",   23)  # час финального парсинга
FINAL_PARSER_MINUTE: int = _int("FINAL_PARSER_MINUTE", 55)  # минута финального парсинга
NEW_DAY_HOUR: int        = _int("NEW_DAY_HOUR",   0)         # час смены дня
NEW_DAY_MINUTE: int      = _int("NEW_DAY_MINUTE", 1)         # минута смены дня

# ── Проверка жюри ─────────────────────────────────────────────────────────────

MAX_WORDS: int  = _int("MAX_WORDS",  100_000)  # максимум слов при вводе
MAX_ERRORS: int = _int("MAX_ERRORS", 10_000)   # максимум ошибок при вводе

# ── Groq AI ───────────────────────────────────────────────────────────────────

GROQ_API_KEY: str    = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL: str      = _str("GROQ_MODEL", "llama-3.3-70b-versatile")
AI_CHUNK_SIZE: int   = _int("AI_CHUNK_SIZE", 3000)  # символов на один запрос


# ── Валидация при старте ──────────────────────────────────────────────────────

def validate() -> None:
    """Вызывается при запуске бота — падает с понятной ошибкой если что-то не так."""
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан в .env")

    if QUEUE_MODE not in ("open", "distributed", "balanced"):
        raise ValueError(f"QUEUE_MODE должен быть 'open', 'distributed' или 'balanced', получили: '{QUEUE_MODE}'")

    if EXPIRE_MINUTES < 1:
        raise ValueError(f"EXPIRE_MINUTES должен быть >= 1, получили: {EXPIRE_MINUTES}")

    if PARSER_INTERVAL < 60:
        raise ValueError("PARSER_INTERVAL должен быть >= 1 минуты")
