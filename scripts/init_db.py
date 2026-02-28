"""
init_db.py — инициализация базы данных с нуля

Создаёт все таблицы если они не существуют.
Безопасно запускать повторно — существующие данные не затрагиваются.

Запуск: python scripts/init_db.py
"""

import sys
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "main.db"

SCHEMA = """
-- Жюри
CREATE TABLE IF NOT EXISTS reviewers (
    TGID    TEXT PRIMARY KEY,
    URL     TEXT NOT NULL,
    Name    TEXT NOT NULL,
    IsAdmin INTEGER NOT NULL DEFAULT 0,
    Verified INTEGER NOT NULL DEFAULT 0
);

-- Авторы постов
CREATE TABLE IF NOT EXISTS authors (
    ID   INTEGER PRIMARY KEY AUTOINCREMENT,
    Name TEXT NOT NULL,
    URL  TEXT NOT NULL UNIQUE
);

-- Дни конкурса
CREATE TABLE IF NOT EXISTS days (
    Day  INTEGER PRIMARY KEY AUTOINCREMENT,
    Data TEXT NOT NULL
);

-- Ссылки на посты
CREATE TABLE IF NOT EXISTS links (
    URL    TEXT PRIMARY KEY,
    Parsed INTEGER NOT NULL DEFAULT 0
);

-- Блэклист ссылок
CREATE TABLE IF NOT EXISTS blacklist (
    URL TEXT PRIMARY KEY
);

-- Посты
CREATE TABLE IF NOT EXISTS posts_info (
    ID             INTEGER PRIMARY KEY AUTOINCREMENT,
    Author         INTEGER NOT NULL REFERENCES authors(ID),
    URL            TEXT    NOT NULL UNIQUE,
    Text           TEXT,
    Day            INTEGER REFERENCES days(Day),
    HumanChecked   INTEGER NOT NULL DEFAULT 0,
    PostOfReviewer INTEGER NOT NULL DEFAULT 0,
    Rejected       INTEGER NOT NULL DEFAULT 0
);

-- Результаты проверки
CREATE TABLE IF NOT EXISTS results (
    ID           INTEGER PRIMARY KEY AUTOINCREMENT,
    Post         INTEGER NOT NULL UNIQUE REFERENCES posts_info(ID),
    BotWords     INTEGER,
    HumanWords   INTEGER,
    HumanErrors  INTEGER,
    Reviewer     TEXT REFERENCES reviewers(TGID),
    SkipReason   TEXT,
    RejectReason TEXT
);

-- Очередь жюри (единая таблица)
CREATE TABLE IF NOT EXISTS queue (
    Reviewer   TEXT    NOT NULL REFERENCES reviewers(TGID),
    Post       INTEGER NOT NULL REFERENCES posts_info(ID),
    AssignedAt TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (Reviewer, Post)
);

-- Индексы
CREATE INDEX IF NOT EXISTS idx_links_parsed      ON links(Parsed);
CREATE INDEX IF NOT EXISTS idx_posts_checked     ON posts_info(HumanChecked);
CREATE INDEX IF NOT EXISTS idx_posts_rejected    ON posts_info(Rejected);
CREATE INDEX IF NOT EXISTS idx_results_reviewer  ON results(Reviewer);
CREATE INDEX IF NOT EXISTS idx_queue_reviewer    ON queue(Reviewer);
CREATE INDEX IF NOT EXISTS idx_queue_post        ON queue(Post);
CREATE INDEX IF NOT EXISTS idx_queue_assignedat   ON queue(AssignedAt);
"""


def init_db() -> None:
    # Создаём папку data если не существует
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    is_new = not DB_PATH.exists()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()

    if is_new:
        print(f"✅ База данных создана: {DB_PATH}")
    else:
        print(f"✅ База данных проверена (уже существует): {DB_PATH}")

    print("\nТаблицы:")
    conn = sqlite3.connect(DB_PATH)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    for t in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
        print(f"  {t[0]:<20} {count} записей")
    conn.close()


if __name__ == "__main__":
    init_db()
