"""
parser/queue_manager.py — управление очередью жюри

Структура:
- Единая таблица queue (Reviewer, Post, AssignedAt)
- При появлении нового поста он идёт жюри с наименьшей текущей очередью
- Если жюри не проверил пост за EXPIRE_MINUTES минут — пост освобождается
- Освобождённые посты доступны любому жюри через /next
"""

import random
from datetime import datetime, timezone
from utils.database import get_db
from utils.logger import setup_logger

log = setup_logger()

EXPIRE_MINUTES = 30  # через сколько минут пост освобождается


# ── Инициализация таблицы ─────────────────────────────────────────────────────

def ensure_queue_table() -> None:
    """Создаёт таблицу queue если не существует."""
    with get_db() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS queue (
                Reviewer   TEXT    NOT NULL,
                Post       INTEGER NOT NULL,
                AssignedAt TEXT    NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (Reviewer, Post),
                FOREIGN KEY (Reviewer) REFERENCES reviewers(TGID),
                FOREIGN KEY (Post)     REFERENCES posts_info(ID)
            )
            """
        )
        # Добавляем колонку AssignedAt если её нет (миграция старой БД)
        try:
            db.execute("ALTER TABLE queue ADD COLUMN AssignedAt TEXT NOT NULL DEFAULT (datetime('now'))")
        except Exception:
            pass  # уже есть
        db.execute("CREATE INDEX IF NOT EXISTS idx_queue_reviewer   ON queue(Reviewer)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_queue_post       ON queue(Post)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_queue_assignedat ON queue(AssignedAt)")
        db.commit()
    log.debug("[queue_manager] Таблица queue готова")


def ensure_all_queues() -> None:
    ensure_queue_table()


def create_queue(tgid: str) -> None:
    ensure_queue_table()


# ── Распределение постов ──────────────────────────────────────────────────────

def _get_reviewer_queue_sizes() -> list[dict]:
    """
    Возвращает верифицированных жюри отсортированных по размеру текущей очереди.
    Распределение по текущей нагрузке — не по истории.
    """
    with get_db() as db:
        rows = db.execute(
            """
            SELECT rv.TGID,
                   COUNT(q.Post) as cnt
            FROM reviewers rv
            LEFT JOIN queue q ON q.Reviewer = rv.TGID
            LEFT JOIN posts_info p ON q.Post = p.ID
                AND p.Rejected     = 0
                AND p.HumanChecked = 0
            WHERE rv.Verified = 1
            GROUP BY rv.TGID
            ORDER BY cnt ASC
            """
        ).fetchall()
    return [{"tgid": r["TGID"], "count": r["cnt"]} for r in rows]


def assign_post(post_id: int) -> str | None:
    """
    Распределяет пост жюри с наименьшей текущей очередью.
    Если несколько одинаковых — выбирает рандомно среди них.
    Возвращает TGID выбранного жюри или None если жюри нет.
    """
    ensure_queue_table()

    reviewers = _get_reviewer_queue_sizes()
    if not reviewers:
        log.warning(f"[queue_manager] Нет верифицированных жюри для поста #{post_id}")
        return None

    min_count  = reviewers[0]["count"]
    candidates = [r for r in reviewers if r["count"] == min_count]
    chosen     = random.choice(candidates)
    tgid       = chosen["tgid"]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO queue (Reviewer, Post, AssignedAt) VALUES (?, ?, ?)",
            (tgid, post_id, now),
        )
        db.commit()

    log.info(f"[queue_manager] Пост #{post_id} → {tgid} (в очереди: {min_count})")
    return tgid


# ── Просроченные посты ────────────────────────────────────────────────────────

def get_expired_posts() -> list[dict]:
    """
    Возвращает посты которые назначены жюри но не проверены за EXPIRE_MINUTES минут.
    Возвращает список {post_id, reviewer_tgid, reviewer_name}.
    """
    ensure_queue_table()
    with get_db() as db:
        rows = db.execute(
            f"""
            SELECT q.Post, q.Reviewer, rv.Name as reviewer_name
            FROM queue q
            JOIN posts_info p ON q.Post = p.ID
            JOIN reviewers rv ON q.Reviewer = rv.TGID
            LEFT JOIN results r ON r.Post = p.ID
            WHERE p.Rejected     = 0
              AND p.HumanChecked  = 0
              AND r.HumanWords    IS NULL
              AND r.Reviewer      IS NULL
              AND datetime(q.AssignedAt) <= datetime('now', '-{EXPIRE_MINUTES} minutes')
            """,
        ).fetchall()
    return [{"post_id": r["Post"], "reviewer_tgid": r["Reviewer"], "reviewer_name": r["reviewer_name"]} for r in rows]


def release_expired_posts() -> list[dict]:
    """
    Освобождает просроченные посты — убирает из очереди конкретного жюри.
    Возвращает список освобождённых постов для уведомлений.
    """
    expired = get_expired_posts()
    if not expired:
        return []

    released = []
    with get_db() as db:
        for item in expired:
            # Убираем из личной очереди жюри
            db.execute(
                "DELETE FROM queue WHERE Reviewer = ? AND Post = ?",
                (item["reviewer_tgid"], item["post_id"]),
            )
            released.append(item)
        db.commit()

    log.info(f"[queue_manager] Освобождено просроченных постов: {len(released)}")
    return released


def get_free_posts() -> list[int]:
    """
    Возвращает посты которые не назначены ни одному жюри — свободные для любого.
    """
    ensure_queue_table()
    with get_db() as db:
        rows = db.execute(
            """
            SELECT p.ID
            FROM posts_info p
            JOIN results r ON r.Post = p.ID
            WHERE p.Rejected      = 0
              AND p.HumanChecked  = 0
              AND p.PostOfReviewer = 0
              AND r.Reviewer IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM queue q WHERE q.Post = p.ID
              )
            ORDER BY p.ID ASC
            """
        ).fetchall()
    return [r["ID"] for r in rows]


# ── Работа с личной очередью ──────────────────────────────────────────────────

def get_queue_posts(tgid: str) -> list[int]:
    """Возвращает список ID постов в личной очереди жюри."""
    ensure_queue_table()
    with get_db() as db:
        rows = db.execute(
            """
            SELECT q.Post FROM queue q
            JOIN posts_info p ON q.Post = p.ID
            WHERE q.Reviewer      = ?
              AND p.Rejected      = 0
              AND p.HumanChecked  = 0
            ORDER BY q.AssignedAt ASC
            """,
            (tgid,),
        ).fetchall()
    return [r["Post"] for r in rows]


def remove_from_queue(tgid: str, post_id: int) -> None:
    """Удаляет пост из очереди конкретного жюри."""
    with get_db() as db:
        db.execute(
            "DELETE FROM queue WHERE Reviewer = ? AND Post = ?",
            (tgid, post_id),
        )
        db.commit()


def remove_from_all_queues(post_id: int) -> None:
    """Удаляет пост из очередей всех жюри (при отклонении или проверке)."""
    with get_db() as db:
        db.execute("DELETE FROM queue WHERE Post = ?", (post_id,))
        db.commit()
    log.info(f"[queue_manager] Пост #{post_id} удалён из очереди")


def get_queue_count(tgid: str) -> int:
    """Возвращает количество постов в очереди жюри."""
    return len(get_queue_posts(tgid))


def get_total_queue_count() -> int:
    """Возвращает суммарное количество уникальных постов во всех очередях."""
    ensure_queue_table()
    with get_db() as db:
        row = db.execute(
            """
            SELECT COUNT(DISTINCT q.Post)
            FROM queue q
            JOIN posts_info p ON q.Post = p.ID
            WHERE p.Rejected = 0 AND p.HumanChecked = 0
            """
        ).fetchone()
    return row[0] if row else 0
