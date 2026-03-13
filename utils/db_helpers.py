"""
utils/db_helpers.py — общие хелперы для работы с БД

Функции используются в нескольких модулях (bot/handlers, scheduler и т.д.)
"""

from utils.database import get_db


def is_registered(tg_id: str) -> bool:
    with get_db() as db:
        return db.execute(
            "SELECT 1 FROM reviewers WHERE TGID=?", (tg_id,)
        ).fetchone() is not None


def is_verified(tg_id: str) -> bool:
    with get_db() as db:
        row = db.execute(
            "SELECT Verified FROM reviewers WHERE TGID=?", (tg_id,)
        ).fetchone()
        return row is not None and row["Verified"] == 1


def is_admin(tg_id: str) -> bool:
    with get_db() as db:
        row = db.execute(
            "SELECT IsAdmin FROM reviewers WHERE TGID=?", (tg_id,)
        ).fetchone()
        return row is not None and row["IsAdmin"] == 1


def get_all_verified_ids() -> list[str]:
    with get_db() as db:
        rows = db.execute(
            "SELECT TGID FROM reviewers WHERE Verified=1"
        ).fetchall()
        return [r["TGID"] for r in rows]


def release_stuck_posts() -> int:
    """
    Сбрасывает посты со статусом 'checking' обратно в 'pending'.
    Вызывается при graceful shutdown, чтобы жюри не потеряли посты.
    Возвращает количество сброшенных постов.
    """
    from utils.constants import PostStatus
    with get_db() as db:
        rows = db.execute(
            "SELECT Post FROM queue WHERE TakenAt IS NOT NULL"
        ).fetchall()
        if not rows:
            return 0
        db.execute(
            f"UPDATE posts_info SET Status=? WHERE Status=?",
            (PostStatus.PENDING, PostStatus.CHECKING),
        )
        db.execute(
            "UPDATE queue SET Reviewer=NULL, TakenAt=NULL WHERE TakenAt IS NOT NULL"
        )
        db.commit()
        return len(rows)
