"""
init_db.py — инициализация базы данных

Запуск: python scripts/init_db.py

Безопасно запускать повторно — существующие данные не затрагиваются.
"""

import sys
import sqlite3
import pathlib

ROOT    = pathlib.Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "main.db"

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── Жюри ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reviewers (
    TGID     TEXT    PRIMARY KEY,
    URL      TEXT    NOT NULL UNIQUE,
    Name     TEXT    NOT NULL,
    IsAdmin  INTEGER NOT NULL DEFAULT 0,
    Verified INTEGER NOT NULL DEFAULT 0
);

-- ── Авторы постов ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS authors (
    ID   INTEGER PRIMARY KEY AUTOINCREMENT,
    Name TEXT    NOT NULL,
    URL  TEXT    NOT NULL UNIQUE
);

-- ── Дни конкурса ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS days (
    Day  INTEGER PRIMARY KEY AUTOINCREMENT,
    Data TEXT    NOT NULL
);

-- ── Ссылки на посты ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS links (
    URL    TEXT    PRIMARY KEY,
    Parsed INTEGER NOT NULL DEFAULT 0
);

-- ── Блэклист ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS blacklist (
    URL TEXT PRIMARY KEY
);

-- ── Посты ────────────────────────────────────────────────────────────────────
-- Status:
--   pending      — спарсен, ждёт жюри
--   checking     — жюри взял, работает прямо сейчас
--   done         — проверка завершена
--   rejected     — отклонён
--   reviewer_post — пост самого жюри, не проверяется
CREATE TABLE IF NOT EXISTS posts_info (
    ID     INTEGER PRIMARY KEY AUTOINCREMENT,
    Author INTEGER NOT NULL REFERENCES authors(ID),
    URL    TEXT    NOT NULL UNIQUE,
    Text   TEXT,
    Day    INTEGER REFERENCES days(Day),
    Status TEXT    NOT NULL DEFAULT 'pending'
        CHECK(Status IN ('pending','checking','done','rejected','reviewer_post'))
);

-- ── Очередь ──────────────────────────────────────────────────────────────────
-- Единственный источник правды о назначении поста.
-- Reviewer NULL  → пост свободен (общая очередь или после истечения времени)
-- TakenAt  NULL  → жюри назначен но ещё не взял через /next
-- TakenAt  NOT NULL → жюри активно работает с постом
CREATE TABLE IF NOT EXISTS queue (
    Post       INTEGER NOT NULL PRIMARY KEY REFERENCES posts_info(ID),
    Reviewer   TEXT    REFERENCES reviewers(TGID),
    AssignedAt TEXT    NOT NULL DEFAULT (datetime('now','utc')),
    TakenAt    TEXT
);

-- ── Результаты проверки ───────────────────────────────────────────────────────
-- Создаётся при парсинге, заполняется жюри.
-- ErrorsPer1000 — вычисляемое поле, не нужно считать в Python.
CREATE TABLE IF NOT EXISTS results (
    ID            INTEGER PRIMARY KEY AUTOINCREMENT,
    Post          INTEGER NOT NULL UNIQUE REFERENCES posts_info(ID),
    BotWords      INTEGER,
    HumanWords    INTEGER,
    HumanErrors   INTEGER,
    ErrorsPer1000 REAL GENERATED ALWAYS AS (
        ROUND(HumanErrors * 1000.0 / NULLIF(HumanWords, 0), 2)
    ) STORED,
    RejectReason  TEXT,
    Reviewer      TEXT REFERENCES reviewers(TGID)
);

-- ── Конфигурация ─────────────────────────────────────────────────────────────
-- queue_mode     : 'open' | 'distributed'
-- expire_minutes : целое число (по умолчанию 30)
CREATE TABLE IF NOT EXISTS config (
    Key   TEXT PRIMARY KEY,
    Value TEXT NOT NULL
);

-- ── Индексы ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_links_parsed       ON links(Parsed);
CREATE INDEX IF NOT EXISTS idx_posts_status       ON posts_info(Status);
CREATE INDEX IF NOT EXISTS idx_queue_reviewer     ON queue(Reviewer);
CREATE INDEX IF NOT EXISTS idx_queue_assignedat   ON queue(AssignedAt);
CREATE INDEX IF NOT EXISTS idx_queue_takenat      ON queue(TakenAt);
CREATE INDEX IF NOT EXISTS idx_results_reviewer   ON results(Reviewer);
"""

DEFAULTS = [
    ("queue_mode",     "distributed"),
    ("expire_minutes", "30"),
]


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not DB_PATH.exists()

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        # Вставляем дефолтные значения конфига только если их нет
        for key, value in DEFAULTS:
            conn.execute(
                "INSERT OR IGNORE INTO config (Key, Value) VALUES (?, ?)",
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()

    status = "создана" if is_new else "проверена (уже существует)"
    print(f"✅ База данных {status}: {DB_PATH}")
    print()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    print("Таблицы:")
    for t in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {t['name']}").fetchone()[0]
        print(f"  {t['name']:<20} {count} записей")

    print()
    print("Конфиг:")
    for row in conn.execute("SELECT Key, Value FROM config ORDER BY Key").fetchall():
        print(f"  {row['Key']:<20} = {row['Value']}")
    conn.close()


if __name__ == "__main__":
    init_db()
