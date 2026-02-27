"""
scripts/drop_queue.py — удаление старой таблицы queue и создание персональных очередей

Запуск: python scripts/drop_queue.py

Что делает:
1. Удаляет старую таблицу queue
2. Создаёт персональные очереди queue_{TGID} для всех верифицированных жюри
3. Перераспределяет посты которые были в старой очереди
"""

import sys
import pathlib

ROOT = pathlib.Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.database import get_db
from parser.queue_manager import create_queue, assign_post, drop_old_queue


def main():
    print("=== Миграция очереди ===\n")

    with get_db() as db:
        # Проверяем существует ли старая таблица queue
        exists = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='queue'"
        ).fetchone()

        if exists:
            # Забираем посты из старой очереди перед удалением
            old_posts = db.execute(
                """
                SELECT q.Post FROM queue q
                JOIN posts_info p ON q.Post = p.ID
                WHERE p.Rejected = 0 AND p.HumanChecked = 0
                """
            ).fetchall()
            old_post_ids = [r["Post"] for r in old_posts]
            print(f"Найдено постов в старой очереди: {len(old_post_ids)}")
        else:
            old_post_ids = []
            print("Старая таблица queue не найдена — пропускаем")

    # Создаём персональные очереди для всех верифицированных жюри
    with get_db() as db:
        reviewers = db.execute(
            "SELECT TGID, Name FROM reviewers WHERE Verified = 1"
        ).fetchall()

    if not reviewers:
        print("⚠️  Нет верифицированных жюри. Сначала верифицируй жюри через бота.")
        sys.exit(1)

    print(f"\nСоздаём очереди для {len(reviewers)} жюри:")
    for r in reviewers:
        create_queue(r["TGID"])
        print(f"  ✅ queue_{r['TGID']} ({r['Name']})")

    # Перераспределяем посты из старой очереди
    if old_post_ids:
        print(f"\nПерераспределяем {len(old_post_ids)} постов...")
        for post_id in old_post_ids:
            tgid = assign_post(post_id)
            if tgid:
                name = next((r["Name"] for r in reviewers if r["TGID"] == tgid), tgid)
                print(f"  Пост #{post_id} → {name}")
            else:
                print(f"  ⚠️  Пост #{post_id} — не удалось распределить")

    # Удаляем старую таблицу
    drop_old_queue()
    print("\n✅ Старая таблица queue удалена")
    print("\n=== Готово ===")


if __name__ == "__main__":
    main()
