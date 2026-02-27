"""
parser/queue_manager.py — управление очередью жюри

Структура:
- Единая таблица queue (Reviewer TEXT, Post INTEGER, PRIMARY KEY (Reviewer, Post))
- При появлении нового поста он распределяется жюри с наименьшим count
- count = проверено + отклонено (считается из results)
- Если у нескольких одинаковый count — выбирается рандомно среди них
"""

import random
from utils.database import get_db
from utils.logger import setup_logger

log = setup_logger()


# ── Инициализация таблицы ─────────────────────────────────────────────────────

def ensure_queue_table() -> None:
    """Создаёт таблицу queue если не существует."""
    with get_db() as db:
        db.execute(
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
        db.execute("CREATE INDEX IF NOT EXISTS idx_queue_reviewer ON queue(Reviewer)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_queue_post     ON queue(Post)")
        db.commit()
    log.debug("[queue_manager] Таблица queue готова")


def ensure_all_queues() -> None:
    """Гарантирует что таблица queue существует (совместимость с вызовами из posts.py)."""
    ensure_queue_table()


def create_queue(tgid: str) -> None:
    """Алиас для совместимости с admin.py. Убеждается что таблица queue существует."""
    ensure_queue_table()


# ── Распределение постов ──────────────────────────────────────────────────────

def _get_reviewer_counts() -> list[dict]:
    """
    Возвращает список верифицированных жюри с их count.
    count = количество проверенных + отклонённых постов.
    """
    with get_db() as db:
        rows = db.execute(
            """
            SELECT rv.TGID,
                   COUNT(r.ID) as cnt
            FROM reviewers rv
            LEFT JOIN results r ON r.Reviewer = rv.TGID
                AND (r.HumanWords IS NOT NULL OR r.RejectReason IS NOT NULL)
            WHERE rv.Verified = 1
            GROUP BY rv.TGID
            ORDER BY cnt ASC
            """
        ).fetchall()
    return [{"tgid": r["TGID"], "count": r["cnt"]} for r in rows]


def assign_post(post_id: int) -> str | None:
    """
    Распределяет пост жюри с наименьшим count.
    Если несколько одинаковых — выбирает рандомно среди них.
    Возвращает TGID выбранного жюри или None если жюри нет.
    """
    ensure_queue_table()

    reviewers = _get_reviewer_counts()
    if not reviewers:
        log.warning(f"[queue_manager] Нет верифицированных жюри для поста #{post_id}")
        return None

    min_count  = reviewers[0]["count"]
    candidates = [r for r in reviewers if r["count"] == min_count]
    chosen     = random.choice(candidates)
    tgid       = chosen["tgid"]

    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO queue (Reviewer, Post) VALUES (?, ?)",
            (tgid, post_id),
        )
        db.commit()

    log.info(f"[queue_manager] Пост #{post_id} → {tgid} (count={min_count})")
    return tgid


# ── Работа с личной очередью ──────────────────────────────────────────────────

def get_queue_posts(tgid: str) -> list[int]:
    """Возвращает список ID постов в очереди жюри."""
    ensure_queue_table()
    with get_db() as db:
        rows = db.execute(
            """
            SELECT q.Post FROM queue q
            JOIN posts_info p ON q.Post = p.ID
            WHERE q.Reviewer      = ?
              AND p.Rejected      = 0
              AND p.HumanChecked  = 0
            ORDER BY q.Post ASC
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
