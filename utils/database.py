"""
database.py — подключение к базе данных

Все модули получают соединение через get_db().
WAL-режим включён для безопасной параллельной работы бота и парсера.
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

    - Автоматически закрывает соединение
    - WAL-режим позволяет боту и парсеру работать одновременно
    - row_factory позволяет обращаться к колонкам по имени: row["Name"]
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()