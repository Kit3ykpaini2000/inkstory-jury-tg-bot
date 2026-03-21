"""
utils/db/posts.py — запросы к таблицам posts_info, queue, results
"""

from utils.database import get_db
from utils.constants import PostStatus


def get_posts_stats() -> dict:
    """Общая статистика по постам."""
    with get_db() as db:
        pending  = db.execute("SELECT COUNT(*) FROM posts_info WHERE Status=?", (PostStatus.PENDING,)).fetchone()[0]
        checking = db.execute("SELECT COUNT(*) FROM posts_info WHERE Status=?", (PostStatus.CHECKING,)).fetchone()[0]
        done     = db.execute("SELECT COUNT(*) FROM posts_info WHERE Status=?", (PostStatus.DONE,)).fetchone()[0]
        rejected = db.execute("SELECT COUNT(*) FROM posts_info WHERE Status=?", (PostStatus.REJECTED,)).fetchone()[0]
        total    = db.execute(
            "SELECT COUNT(*) FROM posts_info WHERE Status NOT IN (?)", (PostStatus.REVIEWER_POST,)
        ).fetchone()[0]
        links    = db.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    return {
        "pending":  pending,
        "checking": checking,
        "done":     done,
        "rejected": rejected,
        "total":    total,
        "links":    links,
    }


def release_stuck_posts() -> int:
    """
    Сбрасывает посты со статусом 'checking' обратно в 'pending'.
    Вызывается при graceful shutdown.
    Возвращает количество сброшенных постов.
    """
    with get_db() as db:
        rows = db.execute(
            "SELECT Post FROM queue WHERE TakenAt IS NOT NULL"
        ).fetchall()
        if not rows:
            return 0
        db.execute(
            "UPDATE posts_info SET Status=? WHERE Status=?",
            (PostStatus.PENDING, PostStatus.CHECKING),
        )
        db.execute(
            "UPDATE queue SET Reviewer=NULL, TakenAt=NULL WHERE TakenAt IS NOT NULL"
        )
        db.commit()
        return len(rows)


def save_result(post_id: int, tgid: str, words: int, errors: int) -> bool:
    """Сохраняет результат проверки поста."""
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT Post FROM queue WHERE Post=? AND Reviewer=? AND TakenAt IS NOT NULL",
            (post_id, tgid),
        ).fetchone()
        if not row:
            db.execute("ROLLBACK")
            return False
        db.execute(
            "UPDATE results SET HumanWords=?, HumanErrors=?, Reviewer=? WHERE Post=?",
            (words, errors, tgid, post_id),
        )
        db.execute(
            "UPDATE posts_info SET Status=? WHERE ID=?",
            (PostStatus.DONE, post_id),
        )
        db.execute("COMMIT")
    return True


def reject_post(post_id: int, tgid: str, reason: str) -> bool:
    """Отклоняет пост."""
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT Post FROM queue WHERE Post=? AND Reviewer=? AND TakenAt IS NOT NULL",
            (post_id, tgid),
        ).fetchone()
        if not row:
            db.execute("ROLLBACK")
            return False
        db.execute(
            "UPDATE posts_info SET Status=? WHERE ID=?",
            (PostStatus.REJECTED, post_id),
        )
        db.execute(
            "UPDATE results SET RejectReason=?, Reviewer=? WHERE Post=?",
            (reason, tgid, post_id),
        )
        db.execute("COMMIT")
    return True


def get_post_text_from_db(post_id: int) -> str | None:
    """Возвращает URL поста для парсинга текста на лету."""
    with get_db() as db:
        row = db.execute(
            "SELECT URL FROM posts_info WHERE ID=?", (post_id,)
        ).fetchone()
    return row["URL"] if row else None


def errors_per_1000(human_errors: int, human_words: int) -> float:
    """Динамически считает ошибки на 1000 слов."""
    if not human_words:
        return 0.0
    return round(human_errors * 1000.0 / human_words, 2)
