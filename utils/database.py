"""
utils/database.py — подключение к БД
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
