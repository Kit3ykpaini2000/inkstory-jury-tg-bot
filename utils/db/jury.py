"""
utils/db/jury.py — запросы к таблице reviewers
"""

from utils.database import get_db
from utils.constants import PostStatus


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


def get_all_reviewers() -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT TGID, Name, URL,
                   COALESCE(Verified, 0) AS Verified,
                   COALESCE(IsAdmin, 0)  AS IsAdmin,
                   COUNT(CASE WHEN r.HumanWords IS NOT NULL THEN 1 END) AS checked
            FROM reviewers rv
            LEFT JOIN results r ON r.Reviewer = rv.TGID
            GROUP BY rv.TGID
            ORDER BY Verified ASC, Name ASC
            """
        ).fetchall()
    return [
        {
            "tgid":     r["TGID"],
            "name":     r["Name"],
            "url":      r["URL"],
            "verified": bool(r["Verified"]),
            "is_admin": bool(r["IsAdmin"]),
            "checked":  r["checked"],
        }
        for r in rows
    ]


def get_reviewer_stats() -> list[dict]:
    """Статистика жюри: сколько проверено/отклонено."""
    with get_db() as db:
        rows = db.execute(
            """
            SELECT
                rv.TGID, rv.Name,
                COUNT(CASE WHEN r.HumanWords IS NOT NULL THEN 1 END) AS checked,
                COUNT(CASE WHEN p.Status=? AND r.Reviewer=rv.TGID THEN 1 END) AS rejected
            FROM reviewers rv
            LEFT JOIN results    r ON r.Reviewer = rv.TGID
            LEFT JOIN posts_info p ON p.ID = r.Post
            GROUP BY rv.TGID
            ORDER BY checked DESC
            """,
            (PostStatus.REJECTED,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_my_stats(tg_id: str) -> dict | None:
    """Статистика конкретного жюри."""
    with get_db() as db:
        row = db.execute(
            """
            SELECT
                rv.Name,
                COUNT(CASE WHEN r.HumanWords IS NOT NULL THEN 1 END) AS checked,
                COUNT(CASE WHEN p.Status=? AND r.Reviewer=rv.TGID THEN 1 END) AS rejected,
                COALESCE(SUM(r.HumanWords),  0) AS total_words,
                COALESCE(SUM(r.HumanErrors), 0) AS total_errors
            FROM reviewers rv
            LEFT JOIN results    r ON r.Reviewer = rv.TGID
            LEFT JOIN posts_info p ON p.ID = r.Post
            WHERE rv.TGID = ?
            GROUP BY rv.TGID
            """,
            (PostStatus.REJECTED, tg_id),
        ).fetchone()
    if not row:
        return None
    return {
        "name":         row["Name"],
        "checked":      row["checked"],
        "rejected":     row["rejected"],
        "total_words":  row["total_words"],
        "total_errors": row["total_errors"],
    }


def register_reviewer(tg_id: str, name: str, url: str) -> None:
    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO reviewers (TGID, URL, Name, IsAdmin, Verified) VALUES (?,?,?,0,0)",
            (tg_id, url, name),
        )
        db.commit()


def set_verified(tg_id: str, value: int) -> None:
    with get_db() as db:
        db.execute("UPDATE reviewers SET Verified=? WHERE TGID=?", (value, tg_id))
        db.commit()


def set_admin(tg_id: str, value: int) -> None:
    with get_db() as db:
        db.execute("UPDATE reviewers SET IsAdmin=? WHERE TGID=?", (value, tg_id))
        db.commit()


def delete_reviewer(tg_id: str) -> None:
    with get_db() as db:
        db.execute("DELETE FROM reviewers WHERE TGID=?", (tg_id,))
        db.commit()
