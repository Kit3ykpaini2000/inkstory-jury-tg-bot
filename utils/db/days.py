"""
utils/db/days.py — запросы к таблице days
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from utils.database import get_db

MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def get_current_day() -> int | None:
    with get_db() as db:
        row = db.execute("SELECT MAX(Day) FROM days").fetchone()
        return row[0] if row and row[0] else None


def get_all_days() -> list[dict]:
    with get_db() as db:
        rows = db.execute("SELECT Day, Data FROM days ORDER BY Day DESC").fetchall()
    return [{"day": r["Day"], "data": r["Data"]} for r in rows]


def create_day(label: str | None = None) -> str:
    today = label or datetime.now(MOSCOW_TZ).strftime("%d.%m.%Y")
    with get_db() as db:
        db.execute("INSERT INTO days (Data) VALUES (?)", (today,))
        db.commit()
    return today


def delete_day(day_id: int, transfer_to: int | None = None) -> None:
    with get_db() as db:
        if transfer_to is not None:
            db.execute(
                "UPDATE posts_info SET Day=? WHERE Day=?", (transfer_to, day_id)
            )
        db.execute("DELETE FROM days WHERE Day=?", (day_id,))
        db.commit()
