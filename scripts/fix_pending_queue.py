"""
scripts/fix_pending_queue.py — добавляет посты со статусом pending, которых нет в очереди.

Уважает текущий режим очереди (QUEUE_MODE из .env):
  open        → queue(Post, Reviewer=NULL)
  distributed → queue(Post, Reviewer=<жюри с наименьшей очередью>)

Использование:
    python scripts/fix_pending_queue.py [--dry-run] [--db PATH]

Флаги:
    --dry-run   Только показать что будет сделано, БД не изменять
    --db PATH   Путь к БД (по умолчанию data/main.db)
"""

import argparse
import random
import sqlite3
import sys
import pathlib
from datetime import datetime, timezone

# ── Пути ──────────────────────────────────────────────────────────────────────

ROOT = pathlib.Path(__file__).parent.parent

def _load_env(env_path: pathlib.Path) -> None:
    """Минимальный загрузчик .env без зависимостей."""
    if not env_path.exists():
        return
    import os
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key not in os.environ:
            os.environ[key] = val

_load_env(ROOT / ".env")

import os
QUEUE_MODE: str = os.getenv("QUEUE_MODE", "distributed")

# ── Утилиты ───────────────────────────────────────────────────────────────────

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _reviewer_with_least_queue(db: sqlite3.Connection) -> str | None:
    """Верифицированный жюри с наименьшей очередью. При ничье — случайный."""
    rows = db.execute(
        """
        SELECT rv.TGID, COUNT(q.Post) AS cnt
        FROM reviewers rv
        LEFT JOIN queue q ON q.Reviewer = rv.TGID
        WHERE rv.Verified = 1
        GROUP BY rv.TGID
        ORDER BY cnt ASC
        """
    ).fetchall()
    if not rows:
        return None
    min_cnt = rows[0]["cnt"]
    candidates = [r["TGID"] for r in rows if r["cnt"] == min_cnt]
    return random.choice(candidates)


# ── Основная логика ───────────────────────────────────────────────────────────

def fix_pending_queue(db_path: pathlib.Path, dry_run: bool) -> None:
    mode = QUEUE_MODE
    if mode not in ("open", "distributed"):
        print(f"[ERROR] Неизвестный QUEUE_MODE={mode!r}. Допустимые: open, distributed")
        sys.exit(1)

    print(f"[INFO] Режим очереди: {mode}")
    print(f"[INFO] БД: {db_path}")
    if dry_run:
        print("[INFO] --dry-run: изменения НЕ применяются\n")

    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row

    # Посты pending без записи в queue
    orphans = conn.execute(
        """
        SELECT p.ID, p.URL, a.Name AS author
        FROM posts_info p
        JOIN authors a ON p.Author = a.ID
        WHERE p.Status = 'pending'
          AND p.ID NOT IN (SELECT Post FROM queue)
        ORDER BY p.ID
        """
    ).fetchall()

    if not orphans:
        print("[OK] Нет постов pending без очереди — всё в порядке.")
        conn.close()
        return

    print(f"[INFO] Найдено постов pending без очереди: {len(orphans)}\n")

    now = _now_utc()
    added = []

    for post in orphans:
        post_id = post["ID"]

        if mode == "open":
            reviewer = None
        else:
            # distributed: назначаем жюри с минимальной нагрузкой
            reviewer = _reviewer_with_least_queue(conn)
            if reviewer is None:
                # Нет верифицированных жюри — кладём как open
                reviewer = None

        reviewer_label = reviewer if reviewer else "общая очередь"
        print(f"  Пост #{post_id} ({post['author']}) → {reviewer_label}")

        if not dry_run:
            conn.execute(
                "INSERT OR IGNORE INTO queue (Post, Reviewer, AssignedAt) VALUES (?, ?, ?)",
                (post_id, reviewer, now),
            )
            # Обновляем счётчик очереди для следующей итерации (в памяти уже обновится
            # при следующем SELECT через тот же conn без commit — поэтому делаем commit
            # после каждой вставки чтобы _reviewer_with_least_queue видел актуальное)
            conn.commit()

        added.append({"post_id": post_id, "reviewer": reviewer})

    if dry_run:
        print(f"\n[DRY-RUN] Было бы добавлено в очередь: {len(added)} постов")
    else:
        print(f"\n[OK] Добавлено в очередь: {len(added)} постов")

    conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Добавляет pending-посты без очереди в queue с учётом QUEUE_MODE"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать что будет сделано, не изменять БД",
    )
    parser.add_argument(
        "--db",
        type=pathlib.Path,
        default=ROOT / "data" / "main.db",
        help="Путь к файлу БД (по умолчанию: data/main.db)",
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"[ERROR] БД не найдена: {args.db}")
        sys.exit(1)

    fix_pending_queue(args.db, dry_run=args.dry_run)


if __name__ == "__main__":
    main()