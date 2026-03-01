"""
parser/queue_manager.py — управление очередью жюри

Три режима (QUEUE_MODE в .env):

  open         — общая очередь, любой жюри берёт первый свободный пост через /next
  distributed  — пост назначается жюри с наименьшей текущей очередью
  balanced     — пост назначается жюри с наименьшей суммарной нагрузкой:
                 проверено + отклонено + в очереди (прошлый вклад учитывается)

В любом режиме:
  - Жюри взял пост (TakenAt NOT NULL) но не проверил за EXPIRE_MINUTES →
      Reviewer=NULL, TakenAt=NULL, Status='pending', уведомляем старого жюри
  - distributed/balanced: пост назначен (Reviewer NOT NULL) но не взят за EXPIRE_MINUTES →
      переназначаем другому жюри, уведомляем нового
  - open: пост был Reviewer=NULL и всё равно не взят — такого быть не может
    (в open посты сразу NULL, их забирают через /next)
"""

import random
from datetime import datetime, timezone

from utils.database import get_db
from utils.config import QUEUE_MODE, EXPIRE_MINUTES
from utils.logger import setup_logger

log = setup_logger()


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ── Выбор жюри ────────────────────────────────────────────────────────────────

def _reviewer_least_queue() -> str | None:
    """
    distributed: жюри с наименьшим количеством постов в текущей очереди.
    При ничье — случайно.
    """
    with get_db() as db:
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

    min_cnt    = rows[0]["cnt"]
    candidates = [r["TGID"] for r in rows if r["cnt"] == min_cnt]
    return random.choice(candidates)


def _reviewer_least_total() -> str | None:
    """
    balanced: жюри с наименьшей суммой (проверено + отклонено + в_очереди).
    При ничье — случайно.
    """
    with get_db() as db:
        rows = db.execute(
            """
            SELECT
                rv.TGID,
                COUNT(DISTINCT q.Post) AS in_queue,
                COUNT(DISTINCT CASE WHEN r.HumanWords IS NOT NULL THEN r.Post END) AS checked,
                COUNT(DISTINCT CASE WHEN p2.Status = 'rejected' AND r2.Reviewer = rv.TGID
                                    THEN r2.Post END) AS rejected
            FROM reviewers rv
            LEFT JOIN queue   q  ON q.Reviewer  = rv.TGID
            LEFT JOIN results r  ON r.Reviewer  = rv.TGID
            LEFT JOIN results r2 ON r2.Reviewer = rv.TGID
            LEFT JOIN posts_info p2 ON p2.ID = r2.Post
            WHERE rv.Verified = 1
            GROUP BY rv.TGID
            ORDER BY (in_queue + checked + rejected) ASC
            """
        ).fetchall()

    if not rows:
        return None

    min_total  = rows[0]["in_queue"] + rows[0]["checked"] + rows[0]["rejected"]
    candidates = [
        r["TGID"] for r in rows
        if r["in_queue"] + r["checked"] + r["rejected"] == min_total
    ]
    return random.choice(candidates)


def _pick_reviewer() -> str | None:
    """Выбирает жюри согласно текущему режиму."""
    if QUEUE_MODE == "distributed":
        return _reviewer_least_queue()
    if QUEUE_MODE == "balanced":
        return _reviewer_least_total()
    return None  # open — без назначения


# ── Добавление в очередь ──────────────────────────────────────────────────────

def assign_post(post_id: int) -> str | None:
    """
    Добавляет пост в очередь согласно текущему режиму.

    open        → queue(Post, Reviewer=NULL)
    distributed → queue(Post, Reviewer=<жюри с мин. очередью>)
    balanced    → queue(Post, Reviewer=<жюри с мин. суммарной нагрузкой>)

    Возвращает TGID назначенного жюри или None (open / нет жюри).
    """
    if QUEUE_MODE == "open":
        with get_db() as db:
            db.execute(
                "INSERT OR IGNORE INTO queue (Post, Reviewer, AssignedAt) VALUES (?,NULL,?)",
                (post_id, _now_utc()),
            )
            db.commit()
        log.info(f"[queue] Пост #{post_id} → общая очередь (open)")
        return None

    tgid = _pick_reviewer()
    if not tgid:
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

    log.info(f"[queue] Пост #{post_id} → {tgid} ({QUEUE_MODE})")
    return tgid


# ── Получение поста жюри ──────────────────────────────────────────────────────

def take_post(tgid: str) -> dict | None:
    """
    Атомарно резервирует следующий пост для жюри (/next).

    Порядок поиска:
    1. Пост уже назначен этому жюри и не взят (Reviewer=tgid, TakenAt IS NULL)
    2. Свободный пост (Reviewer IS NULL, TakenAt IS NULL)

    Возвращает dict или None если нет доступных.
    """
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")

        # 1. Свой назначенный пост
        row = db.execute(
            """
            SELECT q.Post, p.URL, a.Name AS author, r.BotWords
            FROM queue q
            JOIN posts_info p ON q.Post = p.ID
            JOIN authors    a ON p.Author = a.ID
            LEFT JOIN results r ON r.Post = p.ID
            WHERE q.Reviewer = ?
              AND q.TakenAt  IS NULL
              AND p.Status   = 'pending'
            ORDER BY q.AssignedAt ASC
            LIMIT 1
            """,
            (tgid,),
        ).fetchone()

        # 2. Свободный пост
        if not row:
            row = db.execute(
                """
                SELECT q.Post, p.URL, a.Name AS author, r.BotWords
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
        "post_id":   post_id,
        "url":       row["url"],
        "author":    row["author"],
        "bot_words": row["BotWords"],
    }


def get_active_post(tgid: str) -> dict | None:
    """Возвращает пост который жюри взял но ещё не проверил."""
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
    """Освобождает пост обратно в очередь (/cancel)."""
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
    """Удаляет пост из очереди (после проверки или отклонения)."""
    with get_db() as db:
        db.execute("DELETE FROM queue WHERE Post=?", (post_id,))
        db.commit()
    log.info(f"[queue] Пост #{post_id} удалён из очереди")


# ── Просроченные посты ────────────────────────────────────────────────────────

def release_expired_posts() -> list[dict]:
    """
    Освобождает просроченные посты. Возвращает список событий для уведомлений:

      {post_id, reviewer_tgid, type: 'taken'}       — у этого жюри забрали пост
      {post_id, reviewer_tgid, type: 'reassigned'}  — этому жюри назначили пост
      {post_id, type: 'free'}                        — пост стал свободным (open)

    Логика:
      distributed/balanced, TakenAt истёк     → освобождаем + переназначаем другому
      distributed/balanced, AssignedAt истёк  → переназначаем другому (без уведомления старому)
      open, TakenAt истёк                     → освобождаем → type='free' → уведомляем ВСЕХ
    """
    expire   = EXPIRE_MINUTES
    released = []

    with get_db() as db:

        # ── Case 1: жюри взял пост (TakenAt) но не проверил ──────────────────
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
            post_id      = row["Post"]
            old_reviewer = row["Reviewer"]

            # Сбрасываем
            db.execute(
                "UPDATE queue SET Reviewer=NULL, TakenAt=NULL WHERE Post=?",
                (post_id,),
            )
            db.execute(
                "UPDATE posts_info SET Status='pending' WHERE ID=?",
                (post_id,),
            )
            db.commit()
            log.info(f"[queue] Пост #{post_id} истёк (взят) у {old_reviewer}")

            released.append({
                "post_id":       post_id,
                "reviewer_tgid": old_reviewer,
                "type":          "taken",
            })

            if QUEUE_MODE in ("distributed", "balanced"):
                # Переназначаем другому жюри
                new_tgid = _pick_reviewer()
                if new_tgid and new_tgid != old_reviewer:
                    with get_db() as db2:
                        db2.execute(
                            "UPDATE queue SET Reviewer=?, AssignedAt=? WHERE Post=?",
                            (new_tgid, _now_utc(), post_id),
                        )
                        db2.commit()
                    released.append({
                        "post_id":       post_id,
                        "reviewer_tgid": new_tgid,
                        "type":          "reassigned",
                    })
                    log.info(f"[queue] Пост #{post_id} переназначен → {new_tgid}")
                else:
                    # Некому переназначить — остаётся свободным, уведомляем всех
                    released.append({
                        "post_id": post_id,
                        "type":    "free",
                    })
            else:
                # open — пост стал свободным, уведомляем всех жюри
                released.append({
                    "post_id": post_id,
                    "type":    "free",
                })

        # ── Case 2: назначен но не взят (distributed/balanced) ───────────────
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
            post_id      = row["Post"]
            old_reviewer = row["Reviewer"]

            log.info(f"[queue] Пост #{post_id} истёк (назначен) у {old_reviewer}")

            # Переназначаем другому
            new_tgid = _pick_reviewer()
            if new_tgid and new_tgid != old_reviewer:
                with get_db() as db2:
                    db2.execute(
                        "UPDATE queue SET Reviewer=?, AssignedAt=? WHERE Post=?",
                        (new_tgid, _now_utc(), post_id),
                    )
                    db2.commit()
                released.append({
                    "post_id":       post_id,
                    "reviewer_tgid": new_tgid,
                    "type":          "reassigned",
                })
                log.info(f"[queue] Пост #{post_id} переназначен → {new_tgid}")
            else:
                # Некому переназначить — освобождаем совсем
                with get_db() as db2:
                    db2.execute(
                        "UPDATE queue SET Reviewer=NULL WHERE Post=?",
                        (post_id,),
                    )
                    db2.commit()
                released.append({
                    "post_id": post_id,
                    "type":    "free",
                })
                log.info(f"[queue] Пост #{post_id} стал свободным (нет других жюри)")

    taken_cnt      = sum(1 for r in released if r["type"] == "taken")
    reassigned_cnt = sum(1 for r in released if r["type"] == "reassigned")
    free_cnt       = sum(1 for r in released if r["type"] == "free")
    if taken_cnt:
        log.info(f"[queue] Истекло (взятых): {taken_cnt}")
    if reassigned_cnt:
        log.info(f"[queue] Переназначено: {reassigned_cnt}")
    if free_cnt:
        log.info(f"[queue] Стало свободными: {free_cnt}")

    return released


# ── Статистика ────────────────────────────────────────────────────────────────

def get_queue_count(tgid: str) -> int:
    with get_db() as db:
        row = db.execute(
            "SELECT COUNT(*) FROM queue WHERE Reviewer=?", (tgid,)
        ).fetchone()
    return row[0] if row else 0


def get_total_queue_count() -> int:
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
