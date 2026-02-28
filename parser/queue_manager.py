"""
parser/queue_manager.py — управление очередью жюри

Два режима (задаются в таблице config, ключ queue_mode):

  open         — общая очередь, любой жюри берёт первый свободный пост через /next
  distributed  — пост сразу назначается жюри с наименьшей очередью

В обоих режимах:
  - Если жюри взял пост (TakenAt IS NOT NULL) но не проверил за expire_minutes минут
    → Reviewer=NULL, TakenAt=NULL, Status='pending' (пост снова свободен)
  - Если в режиме distributed пост назначен (Reviewer IS NOT NULL)
    но не взят (TakenAt IS NULL) за expire_minutes минут
    → Reviewer=NULL (становится свободным как в open)
"""

import random
from datetime import datetime, timezone

from utils.database import get_db, get_queue_mode, get_expire_minutes
from utils.logger import setup_logger

log = setup_logger()


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ── Распределение ─────────────────────────────────────────────────────────────

def _reviewer_with_least_queue() -> str | None:
    """
    Возвращает TGID верифицированного жюри с наименьшей очередью.
    При ничье выбирает случайно.
    """
    with get_db() as db:
        rows = db.execute(
            """
            SELECT rv.TGID,
                   COUNT(q.Post) AS cnt
            FROM reviewers rv
            LEFT JOIN queue q ON q.Reviewer = rv.TGID
            WHERE rv.Verified = 1
            GROUP BY rv.TGID
            ORDER BY cnt ASC
            """
        ).fetchall()

    if not rows:
        return None

    min_cnt    = rows[0]["cnt"]
    candidates = [r["TGID"] for r in rows if r["cnt"] == min_cnt]
    return random.choice(candidates)


def assign_post(post_id: int) -> str | None:
    """
    Добавляет пост в очередь согласно текущему режиму.

    open        → queue(Post, Reviewer=NULL)
    distributed → queue(Post, Reviewer=<жюри с минимальной очередью>)

    Возвращает TGID назначенного жюри или None (в режиме open или нет жюри).
    """
    mode = get_queue_mode()

    if mode == "open":
        with get_db() as db:
            db.execute(
                "INSERT OR IGNORE INTO queue (Post, Reviewer, AssignedAt) VALUES (?,NULL,?)",
                (post_id, _now_utc()),
            )
            db.commit()
        log.info(f"[queue] Пост #{post_id} → общая очередь (open)")
        return None

    # distributed
    tgid = _reviewer_with_least_queue()
    if not tgid:
        # Жюри нет — кладём в общую как в open
        with get_db() as db:
            db.execute(
                "INSERT OR IGNORE INTO queue (Post, Reviewer, AssignedAt) VALUES (?,NULL,?)",
                (post_id, _now_utc()),
            )
            db.commit()
        log.warning(f"[queue] Нет жюри для поста #{post_id} — в общую очередь")
        return None

    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO queue (Post, Reviewer, AssignedAt) VALUES (?,?,?)",
            (post_id, tgid, _now_utc()),
        )
        db.commit()

    log.info(f"[queue] Пост #{post_id} → {tgid} (distributed)")
    return tgid


# ── Получение поста жюри ──────────────────────────────────────────────────────

def take_post(tgid: str) -> dict | None:
    """
    Атомарно резервирует следующий пост для жюри (вызывается при /next).

    Порядок поиска:
    1. Пост уже назначен этому жюри и не взят (Reviewer=tgid, TakenAt IS NULL)
    2. Свободный пост (Reviewer IS NULL, TakenAt IS NULL)

    Возвращает dict с данными поста или None если нет доступных.
    """
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")

        # 1. Свой назначенный пост
        row = db.execute(
            """
            SELECT q.Post, p.URL, a.Name AS author, r.BotWords,
                   q.AssignedAt, q.TakenAt
            FROM queue q
            JOIN posts_info p ON q.Post = p.ID
            JOIN authors    a ON p.Author = a.ID
            LEFT JOIN results r ON r.Post = p.ID
            WHERE q.Reviewer  = ?
              AND q.TakenAt   IS NULL
              AND p.Status    = 'pending'
            ORDER BY q.AssignedAt ASC
            LIMIT 1
            """,
            (tgid,),
        ).fetchone()

        # 2. Свободный пост
        if not row:
            row = db.execute(
                """
                SELECT q.Post, p.URL, a.Name AS author, r.BotWords,
                       q.AssignedAt, q.TakenAt
                FROM queue q
                JOIN posts_info p ON q.Post = p.ID
                JOIN authors    a ON p.Author = a.ID
                LEFT JOIN results r ON r.Post = p.ID
                WHERE q.Reviewer IS NULL
                  AND q.TakenAt  IS NULL
                  AND p.Status   = 'pending'
                ORDER BY q.AssignedAt ASC
                LIMIT 1
                """,
            ).fetchone()

        if not row:
            db.execute("ROLLBACK")
            return None

        post_id = row["Post"]
        now     = _now_utc()

        # Резервируем: назначаем жюри и ставим TakenAt
        db.execute(
            "UPDATE queue SET Reviewer=?, TakenAt=? WHERE Post=? AND TakenAt IS NULL",
            (tgid, now, post_id),
        )
        db.execute(
            "UPDATE posts_info SET Status='checking' WHERE ID=? AND Status='pending'",
            (post_id,),
        )
        db.execute("COMMIT")

    log.info(f"[queue] Жюри {tgid} взял пост #{post_id}")
    return {
        "post_id": post_id,
        "url":     row["url"],
        "author":  row["author"],
        "bot_words": row["BotWords"],
    }


def get_active_post(tgid: str) -> dict | None:
    """Возвращает пост который жюри уже взял но ещё не проверил."""
    with get_db() as db:
        row = db.execute(
            """
            SELECT q.Post, p.URL, a.Name AS author, r.BotWords
            FROM queue q
            JOIN posts_info p ON q.Post = p.ID
            JOIN authors    a ON p.Author = a.ID
            LEFT JOIN results r ON r.Post = p.ID
            WHERE q.Reviewer = ?
              AND q.TakenAt  IS NOT NULL
              AND p.Status   = 'checking'
            LIMIT 1
            """,
            (tgid,),
        ).fetchone()

    if not row:
        return None
    return {
        "post_id":   row["Post"],
        "url":       row["url"],
        "author":    row["author"],
        "bot_words": row["BotWords"],
    }


def release_post(tgid: str, post_id: int) -> None:
    """Освобождает пост обратно в очередь (отмена через /cancel)."""
    with get_db() as db:
        db.execute(
            "UPDATE queue SET Reviewer=NULL, TakenAt=NULL WHERE Post=? AND Reviewer=?",
            (post_id, tgid),
        )
        db.execute(
            "UPDATE posts_info SET Status='pending' WHERE ID=? AND Status='checking'",
            (post_id,),
        )
        db.commit()
    log.info(f"[queue] Пост #{post_id} освобождён жюри {tgid}")


def remove_post(post_id: int) -> None:
    """Удаляет пост из очереди полностью (после проверки или отклонения)."""
    with get_db() as db:
        db.execute("DELETE FROM queue WHERE Post=?", (post_id,))
        db.commit()
    log.info(f"[queue] Пост #{post_id} удалён из очереди")


# ── Просроченные посты ────────────────────────────────────────────────────────

def release_expired_posts() -> list[dict]:
    """
    Освобождает просроченные посты.

    Два случая:
    1. Жюри взял пост (TakenAt IS NOT NULL) но не проверил за expire_minutes
       → Reviewer=NULL, TakenAt=NULL, Status='pending'
       → уведомляем жюри что у него забрали пост

    2. В режиме distributed пост назначен (Reviewer IS NOT NULL)
       но не взят (TakenAt IS NULL) за expire_minutes
       → Reviewer=NULL (становится свободным)
       → без уведомления (жюри ещё даже не видел пост)

    Возвращает список {post_id, reviewer_tgid, type: 'taken'|'assigned'}
    только для case 1 (для уведомлений).
    """
    expire = get_expire_minutes()
    released = []

    with get_db() as db:
        # Case 1: взят но не проверен
        taken = db.execute(
            f"""
            SELECT q.Post, q.Reviewer
            FROM queue q
            JOIN posts_info p ON q.Post = p.ID
            WHERE q.TakenAt IS NOT NULL
              AND p.Status = 'checking'
              AND datetime(q.TakenAt) <= datetime('now', '-{expire} minutes')
            """
        ).fetchall()

        for row in taken:
            db.execute(
                "UPDATE queue SET Reviewer=NULL, TakenAt=NULL WHERE Post=?",
                (row["Post"],),
            )
            db.execute(
                "UPDATE posts_info SET Status='pending' WHERE ID=?",
                (row["Post"],),
            )
            released.append({
                "post_id":       row["Post"],
                "reviewer_tgid": row["Reviewer"],
                "type":          "taken",
            })
            log.info(f"[queue] Пост #{row['Post']} истёк (взят) у {row['Reviewer']}")

        # Case 2: назначен но не взят (только distributed)
        assigned = db.execute(
            f"""
            SELECT q.Post, q.Reviewer
            FROM queue q
            JOIN posts_info p ON q.Post = p.ID
            WHERE q.Reviewer IS NOT NULL
              AND q.TakenAt  IS NULL
              AND p.Status   = 'pending'
              AND datetime(q.AssignedAt) <= datetime('now', '-{expire} minutes')
            """
        ).fetchall()

        for row in assigned:
            db.execute(
                "UPDATE queue SET Reviewer=NULL WHERE Post=?",
                (row["Post"],),
            )
            log.info(f"[queue] Пост #{row['Post']} истёк (назначен) у {row['Reviewer']}")

        db.commit()

    if released:
        log.info(f"[queue] Освобождено просроченных (взятых): {len(released)}")
    if assigned:
        log.info(f"[queue] Освобождено просроченных (назначенных): {len(assigned)}")

    return released


# ── Статистика ────────────────────────────────────────────────────────────────

def get_queue_count(tgid: str) -> int:
    """Количество постов в очереди конкретного жюри."""
    with get_db() as db:
        row = db.execute(
            "SELECT COUNT(*) FROM queue WHERE Reviewer=?", (tgid,)
        ).fetchone()
    return row[0] if row else 0


def get_total_queue_count() -> int:
    """Общее количество постов в очереди (уникальных)."""
    with get_db() as db:
        row = db.execute(
            """
            SELECT COUNT(*) FROM queue q
            JOIN posts_info p ON q.Post = p.ID
            WHERE p.Status IN ('pending', 'checking')
            """
        ).fetchone()
    return row[0] if row else 0


def get_free_posts_count() -> int:
    """Количество свободных постов (Reviewer IS NULL)."""
    with get_db() as db:
        row = db.execute(
            """
            SELECT COUNT(*) FROM queue q
            JOIN posts_info p ON q.Post = p.ID
            WHERE q.Reviewer IS NULL AND p.Status = 'pending'
            """
        ).fetchone()
    return row[0] if row else 0


def get_all_reviewer_queue_sizes() -> list[dict]:
    """Возвращает размер очереди каждого верифицированного жюри."""
    with get_db() as db:
        rows = db.execute(
            """
            SELECT rv.TGID, rv.Name, COUNT(q.Post) AS cnt
            FROM reviewers rv
            LEFT JOIN queue q ON q.Reviewer = rv.TGID
            WHERE rv.Verified = 1
            GROUP BY rv.TGID
            ORDER BY cnt DESC
            """
        ).fetchall()
    return [{"tgid": r["TGID"], "name": r["Name"], "count": r["cnt"]} for r in rows]
