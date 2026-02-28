"""
utils/database.py — подключение к БД и хелперы конфига
"""

import sqlite3
import pathlib
from contextlib import contextmanager

DB_PATH = pathlib.Path(__file__).parent.parent / "data" / "main.db"


@contextmanager
def get_db():
    """
    Контекстный менеджер для работы с БД.

    Использование:
        with get_db() as db:
            db.execute("SELECT ...")

    WAL-режим позволяет боту и парсеру работать одновременно.
    row_factory позволяет обращаться к колонкам по имени: row["Name"]
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_config(key: str, default: str | None = None) -> str | None:
    """Читает значение из таблицы config."""
    with get_db() as db:
        row = db.execute(
            "SELECT Value FROM config WHERE Key = ?", (key,)
        ).fetchone()
        return row["Value"] if row else default


def set_config(key: str, value: str) -> None:
    """Записывает значение в таблицу config."""
    with get_db() as db:
        db.execute(
            "INSERT INTO config (Key, Value) VALUES (?, ?) "
            "ON CONFLICT(Key) DO UPDATE SET Value = excluded.Value",
            (key, value),
        )
        db.commit()


def get_queue_mode() -> str:
    """Возвращает текущий режим очереди: 'open' или 'distributed'."""
    return get_config("queue_mode", "distributed")


def get_expire_minutes() -> int:
    """Возвращает время истечения резервации в минутах."""
    return int(get_config("expire_minutes", "30"))
