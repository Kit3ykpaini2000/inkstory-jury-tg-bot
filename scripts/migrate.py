import sys as _sys
import pathlib as _pathlib
_ROOT = _pathlib.Path(__file__).parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

"""
migrate.py — перенос данных из старой БД (v6, с queue_{TGID}) в новую (v7, единая queue)

Запуск: python scripts/migrate.py --old path/to/old.db --new path/to/new.db

Что переносится:
- reviewers  : TGID, URL, Name, IsAdmin, Verified
- authors    : ID, Name, URL
- days       : Day, Data
- links      : URL, Parsed
- blacklist  : URL
- posts_info : ID, Author, URL, Text, Day, HumanChecked, PostOfReviewer, Rejected
- queue      : переносим из всех таблиц queue_{TGID} в единую queue(Reviewer, Post)
- results    : ID, Post, BotWords, HumanWords, HumanErrors, Reviewer, SkipReason, RejectReason
"""

import sqlite3
import argparse
import pathlib
import sys
import re


def get_conn(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def migrate(old_path: str, new_path: str) -> None:
    print(f"Старая БД : {old_path}")
    print(f"Новая БД  : {new_path}")
    print()

    old = get_conn(old_path)
    new = get_conn(new_path)

    try:
        _migrate_reviewers(old, new)
        _migrate_authors(old, new)
        _migrate_days(old, new)
        _migrate_links(old, new)
        _migrate_blacklist(old, new)
        _migrate_posts(old, new)
        _migrate_queue(old, new)
        _migrate_results(old, new)
        print("\n✅ Миграция завершена успешно.")
    except Exception as e:
        print(f"\n❌ Ошибка миграции: {e}")
        raise
    finally:
        old.close()
        new.close()


# ── Таблицы ───────────────────────────────────────────────────────────────────

def _migrate_reviewers(old: sqlite3.Connection, new: sqlite3.Connection) -> None:
    rows = old.execute("SELECT TGID, URL, Name, IsAdmin, Verified FROM reviewers").fetchall()
    new.executemany(
        "INSERT OR IGNORE INTO reviewers (TGID, URL, Name, IsAdmin, Verified) VALUES (?, ?, ?, ?, ?)",
        [(r["TGID"], r["URL"], r["Name"], r["IsAdmin"], r["Verified"]) for r in rows],
    )
    new.commit()
    print(f"reviewers  : {len(rows)} записей")


def _migrate_authors(old: sqlite3.Connection, new: sqlite3.Connection) -> None:
    rows = old.execute("SELECT ID, Name, URL FROM authors").fetchall()
    new.executemany(
        "INSERT OR IGNORE INTO authors (ID, Name, URL) VALUES (?, ?, ?)",
        [(r["ID"], r["Name"], r["URL"]) for r in rows],
    )
    new.commit()
    print(f"authors    : {len(rows)} записей")


def _migrate_days(old: sqlite3.Connection, new: sqlite3.Connection) -> None:
    rows = old.execute("SELECT Day, Data FROM days").fetchall()
    new.executemany(
        "INSERT OR IGNORE INTO days (Day, Data) VALUES (?, ?)",
        [(r["Day"], r["Data"]) for r in rows],
    )
    new.commit()
    print(f"days       : {len(rows)} записей")


def _migrate_links(old: sqlite3.Connection, new: sqlite3.Connection) -> None:
    rows = old.execute("SELECT URL, Parsed FROM links").fetchall()
    new.executemany(
        "INSERT OR IGNORE INTO links (URL, Parsed) VALUES (?, ?)",
        [(r["URL"], r["Parsed"]) for r in rows],
    )
    new.commit()
    print(f"links      : {len(rows)} записей")


def _migrate_blacklist(old: sqlite3.Connection, new: sqlite3.Connection) -> None:
    rows = old.execute("SELECT URL FROM blacklist").fetchall()
    new.executemany(
        "INSERT OR IGNORE INTO blacklist (URL) VALUES (?)",
        [(r["URL"],) for r in rows],
    )
    new.commit()
    print(f"blacklist  : {len(rows)} записей")


def _migrate_posts(old: sqlite3.Connection, new: sqlite3.Connection) -> None:
    rows = old.execute(
        """
        SELECT ID, Author, URL, Text, Day,
               HumanChecked, PostOfReviewer, Rejected
        FROM posts_info
        """
    ).fetchall()
    new.executemany(
        """
        INSERT OR IGNORE INTO posts_info
            (ID, Author, URL, Text, Day, HumanChecked, PostOfReviewer, Rejected)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r["ID"], r["Author"], r["URL"], r["Text"], r["Day"],
                r["HumanChecked"], r["PostOfReviewer"], r["Rejected"],
            )
            for r in rows
        ],
    )
    new.commit()
    print(f"posts_info : {len(rows)} записей")


def _migrate_queue(old: sqlite3.Connection, new: sqlite3.Connection) -> None:
    """
    Переносит данные из персональных таблиц queue_{TGID} (старая схема)
    в единую таблицу queue(Reviewer, Post) (новая схема).
    """
    # Создаём новую таблицу если нет
    new.execute(
        """
        CREATE TABLE IF NOT EXISTS queue (
            Reviewer TEXT    NOT NULL,
            Post     INTEGER NOT NULL,
            PRIMARY KEY (Reviewer, Post),
            FOREIGN KEY (Reviewer) REFERENCES reviewers(TGID),
            FOREIGN KEY (Post)     REFERENCES posts_info(ID)
        )
        """
    )
    new.execute("CREATE INDEX IF NOT EXISTS idx_queue_reviewer ON queue(Reviewer)")
    new.execute("CREATE INDEX IF NOT EXISTS idx_queue_post     ON queue(Post)")
    new.commit()

    # Ищем все таблицы вида queue_{TGID} в старой БД
    tables = old.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'queue_%'"
    ).fetchall()

    total = 0
    for table_row in tables:
        table_name = table_row["name"]
        # Извлекаем TGID из имени таблицы
        match = re.match(r"^queue_(\d+)$", table_name)
        if not match:
            print(f"  Пропускаем таблицу {table_name} (не похожа на queue_{{TGID}})")
            continue

        tgid = match.group(1)
        rows = old.execute(f"SELECT Post FROM {table_name}").fetchall()  # noqa: S608

        if rows:
            new.executemany(
                "INSERT OR IGNORE INTO queue (Reviewer, Post) VALUES (?, ?)",
                [(tgid, r["Post"]) for r in rows],
            )
            new.commit()
            print(f"  {table_name} → queue (reviewer={tgid}): {len(rows)} записей")
            total += len(rows)

    if not tables:
        # Старая БД уже может иметь единую queue без TGID (совсем старые версии)
        try:
            rows = old.execute("SELECT Post FROM queue").fetchall()
            print(f"  Старая единая queue без Reviewer: {len(rows)} записей — пропускаем (нет привязки к жюри)")
        except Exception:
            pass

    print(f"queue      : {total} записей перенесено")


def _migrate_results(old: sqlite3.Connection, new: sqlite3.Connection) -> None:
    # Получаем ID постов жюри — их результаты не переносим
    reviewer_posts = {
        row[0]
        for row in new.execute(
            "SELECT ID FROM posts_info WHERE PostOfReviewer = 1"
        ).fetchall()
    }

    rows = old.execute(
        """
        SELECT ID, Post, BotWords, HumanWords, HumanErrors,
               Reviewer, SkipReason, RejectReason
        FROM results
        """
    ).fetchall()

    to_insert = [
        (
            r["ID"], r["Post"], r["BotWords"], r["HumanWords"], r["HumanErrors"],
            r["Reviewer"], r["SkipReason"], r["RejectReason"],
        )
        for r in rows
        if r["Post"] not in reviewer_posts
    ]

    skipped = len(rows) - len(to_insert)

    new.executemany(
        """
        INSERT OR REPLACE INTO results
            (ID, Post, BotWords, HumanWords, HumanErrors,
             Reviewer, SkipReason, RejectReason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        to_insert,
    )
    new.commit()
    print(f"results    : {len(to_insert)} записей (пропущено постов жюри: {skipped})")


# ── Точка входа ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Миграция БД inkstory v6 → v7 (единая таблица queue)")
    parser.add_argument("--old", required=True, help="Путь к старой БД (v6, с queue_{TGID})")
    parser.add_argument("--new", required=True, help="Путь к новой БД (v7, с единой queue)")
    args = parser.parse_args()

    old_path = pathlib.Path(args.old)
    new_path = pathlib.Path(args.new)

    if not old_path.exists():
        print(f"❌ Старая БД не найдена: {old_path}")
        sys.exit(1)

    if not new_path.exists():
        print(f"❌ Новая БД не найдена: {new_path}")
        sys.exit(1)

    migrate(str(old_path), str(new_path))


if __name__ == "__main__":
    main()
