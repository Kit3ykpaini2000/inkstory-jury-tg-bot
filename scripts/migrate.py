"""
migrate.py — миграция данных из старой БД в новую схему

Запуск: python scripts/migrate.py --old path/to/old.db --new path/to/new.db

Что переносится:
  reviewers  — без изменений
  authors    — без изменений
  days       — без изменений
  links      — без изменений
  blacklist  — без изменений
  posts_info — HumanChecked/Rejected/PostOfReviewer → Status
  results    — без Reviewer/SkipReason (они теперь в queue)
  queue      — добавляем AssignedAt/TakenAt, поддержка старых queue_{TGID}

Что НЕ переносится:
  - Посты у которых results.Reviewer IS NOT NULL но HumanWords IS NULL
    (жюри взял но не проверил) — сбрасываются в pending
"""

import sys
import re
import argparse
import pathlib
import sqlite3
from datetime import datetime, timezone


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")  # отключаем на время миграции
    conn.row_factory = sqlite3.Row
    return conn


def _post_status(human_checked: int, rejected: int, post_of_reviewer: int) -> str:
    """Конвертирует три старых булевых поля в новый Status."""
    if post_of_reviewer:
        return "reviewer_post"
    if rejected:
        return "rejected"
    if human_checked:
        return "done"
    return "pending"


# ── Таблицы без изменений ─────────────────────────────────────────────────────

def migrate_reviewers(old: sqlite3.Connection, new: sqlite3.Connection) -> int:
    rows = old.execute(
        "SELECT TGID, URL, Name, IsAdmin, Verified FROM reviewers"
    ).fetchall()
    new.executemany(
        "INSERT OR IGNORE INTO reviewers (TGID, URL, Name, IsAdmin, Verified) VALUES (?,?,?,?,?)",
        [(r["TGID"], r["URL"], r["Name"], r["IsAdmin"] or 0, r["Verified"] or 0) for r in rows],
    )
    new.commit()
    return len(rows)


def migrate_authors(old: sqlite3.Connection, new: sqlite3.Connection) -> int:
    rows = old.execute("SELECT ID, Name, URL FROM authors").fetchall()
    new.executemany(
        "INSERT OR IGNORE INTO authors (ID, Name, URL) VALUES (?,?,?)",
        [(r["ID"], r["Name"], r["URL"]) for r in rows],
    )
    new.commit()
    return len(rows)


def migrate_days(old: sqlite3.Connection, new: sqlite3.Connection) -> int:
    rows = old.execute("SELECT Day, Data FROM days").fetchall()
    new.executemany(
        "INSERT OR IGNORE INTO days (Day, Data) VALUES (?,?)",
        [(r["Day"], r["Data"]) for r in rows],
    )
    new.commit()
    return len(rows)


def migrate_links(old: sqlite3.Connection, new: sqlite3.Connection) -> int:
    rows = old.execute("SELECT URL, Parsed FROM links").fetchall()
    new.executemany(
        "INSERT OR IGNORE INTO links (URL, Parsed) VALUES (?,?)",
        [(r["URL"], r["Parsed"] or 0) for r in rows],
    )
    new.commit()
    return len(rows)


def migrate_blacklist(old: sqlite3.Connection, new: sqlite3.Connection) -> int:
    rows = old.execute("SELECT URL FROM blacklist").fetchall()
    new.executemany(
        "INSERT OR IGNORE INTO blacklist (URL) VALUES (?)",
        [(r["URL"],) for r in rows],
    )
    new.commit()
    return len(rows)


# ── posts_info — конвертируем статусы ─────────────────────────────────────────

def migrate_posts(old: sqlite3.Connection, new: sqlite3.Connection) -> int:
    # Проверяем какие колонки есть в старой таблице
    cols = {row[1] for row in old.execute("PRAGMA table_info(posts_info)").fetchall()}

    rows = old.execute("SELECT * FROM posts_info").fetchall()
    inserted = 0

    for r in rows:
        human_checked   = r["HumanChecked"]   if "HumanChecked"   in cols else 0
        rejected        = r["Rejected"]        if "Rejected"        in cols else 0
        post_of_reviewer= r["PostOfReviewer"]  if "PostOfReviewer"  in cols else 0
        status = _post_status(
            human_checked or 0,
            rejected or 0,
            post_of_reviewer or 0,
        )

        new.execute(
            """
            INSERT OR IGNORE INTO posts_info (ID, Author, URL, Text, Day, Status)
            VALUES (?,?,?,?,?,?)
            """,
            (r["ID"], r["Author"], r["URL"], r["Text"], r["Day"], status),
        )
        inserted += 1

    new.commit()
    return inserted


# ── results — убираем Reviewer/SkipReason ────────────────────────────────────

def migrate_results(old: sqlite3.Connection, new: sqlite3.Connection) -> int:
    cols = {row[1] for row in old.execute("PRAGMA table_info(results)").fetchall()}
    rows = old.execute("SELECT * FROM results").fetchall()
    inserted = 0

    for r in rows:
        # Пропускаем посты-жюри
        post_status = new.execute(
            "SELECT Status FROM posts_info WHERE ID=?", (r["Post"],)
        ).fetchone()
        if post_status and post_status["Status"] == "reviewer_post":
            continue

        # Берём reviewer из старой таблицы только если проверка завершена
        reviewer = None
        if "Reviewer" in cols and r["HumanWords"] is not None:
            reviewer = r["Reviewer"]

        new.execute(
            """
            INSERT OR IGNORE INTO results
                (ID, Post, BotWords, HumanWords, HumanErrors, RejectReason, Reviewer)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                r["ID"], r["Post"], r["BotWords"],
                r["HumanWords"], r["HumanErrors"],
                r["RejectReason"] if "RejectReason" in cols else None,
                reviewer,
            ),
        )
        inserted += 1

    new.commit()
    return inserted


# ── queue — поддержка и старой и новой схемы ──────────────────────────────────

def migrate_queue(old: sqlite3.Connection, new: sqlite3.Connection) -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    inserted = 0

    # Проверяем новую единую таблицу queue
    old_tables = {
        row["name"]
        for row in old.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    if "queue" in old_tables:
        cols = {row[1] for row in old.execute("PRAGMA table_info(queue)").fetchall()}

        if "Reviewer" in cols:
            # Новая схема (Reviewer, Post) — просто переносим
            rows = old.execute(
                """
                SELECT q.Post, q.Reviewer
                FROM queue q
                JOIN posts_info p ON q.Post = p.ID
                WHERE p.Rejected = 0 AND p.HumanChecked = 0
                """
            ).fetchall() if "HumanChecked" in {
                row[1] for row in old.execute("PRAGMA table_info(posts_info)").fetchall()
            } else old.execute("SELECT Post, Reviewer FROM queue").fetchall()

            for r in rows:
                # Проверяем что пост существует в новой БД и ещё pending
                status = new.execute(
                    "SELECT Status FROM posts_info WHERE ID=?", (r["Post"],)
                ).fetchone()
                if not status or status["Status"] not in ("pending", "checking"):
                    continue
                new.execute(
                    "INSERT OR IGNORE INTO queue (Post, Reviewer, AssignedAt) VALUES (?,?,?)",
                    (r["Post"], r["Reviewer"], now),
                )
                inserted += 1
        else:
            # Совсем старая схема без Reviewer — пропускаем
            print("  queue: старая схема без Reviewer — пропускаем")

    # Ищем персональные таблицы queue_{TGID} (очень старая схема)
    for table_name in old_tables:
        match = re.match(r"^queue_(\d+)$", table_name)
        if not match:
            continue
        tgid = match.group(1)
        rows = old.execute(f"SELECT Post FROM [{table_name}]").fetchall()  # noqa: S608
        for r in rows:
            status = new.execute(
                "SELECT Status FROM posts_info WHERE ID=?", (r["Post"],)
            ).fetchone()
            if not status or status["Status"] not in ("pending", "checking"):
                continue
            new.execute(
                "INSERT OR IGNORE INTO queue (Post, Reviewer, AssignedAt) VALUES (?,?,?)",
                (r["Post"], tgid, now),
            )
            inserted += 1
        if rows:
            print(f"  {table_name} → queue: {len(rows)} записей")

    new.commit()
    return inserted


# ── Основная функция ──────────────────────────────────────────────────────────

def migrate(old_path: str, new_path: str) -> None:
    print(f"Старая БД : {old_path}")
    print(f"Новая БД  : {new_path}")
    print()

    old = connect(old_path)
    new = connect(new_path)

    steps = [
        ("reviewers",  migrate_reviewers),
        ("authors",    migrate_authors),
        ("days",       migrate_days),
        ("links",      migrate_links),
        ("blacklist",  migrate_blacklist),
        ("posts_info", migrate_posts),
        ("results",    migrate_results),
        ("queue",      migrate_queue),
    ]

    try:
        for name, fn in steps:
            count = fn(old, new)
            print(f"  {name:<15} {count} записей")
        print("\n✅ Миграция завершена успешно.")
    except Exception as e:
        print(f"\n❌ Ошибка миграции: {e}")
        raise
    finally:
        old.close()
        new.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Миграция БД inkstory в новую схему"
    )
    parser.add_argument("--old", required=True, help="Путь к старой БД")
    parser.add_argument("--new", required=True, help="Путь к новой (уже инициализированной) БД")
    args = parser.parse_args()

    old_path = pathlib.Path(args.old)
    new_path = pathlib.Path(args.new)

    if not old_path.exists():
        print(f"❌ Старая БД не найдена: {old_path}")
        sys.exit(1)
    if not new_path.exists():
        print(f"❌ Новая БД не найдена: {new_path}")
        print("   Сначала запусти: python scripts/init_db.py")
        sys.exit(1)

    migrate(str(old_path), str(new_path))


if __name__ == "__main__":
    main()
