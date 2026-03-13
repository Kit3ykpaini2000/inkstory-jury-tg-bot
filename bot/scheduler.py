"""
bot/scheduler.py — задачи планировщика

Автоматические задачи:
- Каждые N минут — парсер + назначение постов + уведомление жюри
- Каждые 5 минут — освобождение просроченных постов
- 23:55 МСК — финальный парсинг
- 00:01 МСК — создание нового дня
"""

import asyncio
import functools
from datetime import datetime
from zoneinfo import ZoneInfo

from utils.database import get_db
from utils.logger import setup_logger
from utils.db_helpers import get_all_verified_ids
from parser.queue_manager import (
    assign_post, release_expired_posts, get_free_posts_count,
)

log = setup_logger()

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
_parser_lock = asyncio.Lock()


# ── Хелперы ───────────────────────────────────────────────────────────────────

def _create_new_day() -> str:
    today = datetime.now(MOSCOW_TZ).strftime("%d.%m.%Y")
    with get_db() as db:
        db.execute("INSERT INTO days (Data) VALUES (?)", (today,))
        db.commit()
    return today


def _run_parser_sync() -> list[int]:
    """Запускает парсер синхронно (выполняется в executor)."""
    from parser.links import parse as parse_links
    from parser.posts import parse as parse_posts
    parse_links()
    return parse_posts()


async def _run_parser() -> list[int] | None:
    """Запускает парсер асинхронно с блокировкой от параллельного запуска."""
    if _parser_lock.locked():
        log.warning("[parser] Уже запущен, пропускаем")
        return None

    async with _parser_lock:
        try:
            loop     = asyncio.get_running_loop()
            post_ids = await loop.run_in_executor(None, functools.partial(_run_parser_sync))
            log.info(f"[parser] Готово. Новых постов: {len(post_ids) if post_ids else 0}")
            return post_ids if isinstance(post_ids, list) else []
        except Exception as e:
            log.exception(f"[parser] Исключение: {e}")
            return None


async def _notify_after_parse(bot, post_ids: list[int]) -> None:
    """
    Назначает посты в очередь и уведомляет жюри.

    balanced — уведомляем каждого жюри персонально о его новых постах.
    open     — уведомляем всех верифицированных жюри об общем количестве.
    """
    assigned: dict[str, int] = {}  # tgid → количество
    open_count = 0

    for post_id in post_ids:
        tgid = assign_post(post_id)
        if tgid:
            assigned[tgid] = assigned.get(tgid, 0) + 1
        else:
            open_count += 1

    for tg_id, count in assigned.items():
        try:
            await bot.send_message(
                chat_id=tg_id,
                text=(
                    f"📬 Появились новые посты!\n\n"
                    f"🆕 Тебе добавлено: {count}\n\n"
                    f"/next — взять пост"
                ),
            )
        except Exception as e:
            log.warning(f"[scheduler] Не удалось уведомить {tg_id}: {e}")

    if open_count > 0:
        for tg_id in get_all_verified_ids():
            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text=(
                        f"📬 Появились новые посты!\n\n"
                        f"🆕 Новых в очереди: {open_count}\n\n"
                        f"/next — взять пост"
                    ),
                )
            except Exception as e:
                log.warning(f"[scheduler] Не удалось уведомить {tg_id}: {e}")


# ── Задачи ────────────────────────────────────────────────────────────────────

async def job_auto_parser(context):
    log.info("[job] Автозапуск парсера")
    post_ids = await _run_parser()
    if post_ids is None:
        return
    await _notify_after_parse(context.bot, post_ids)


async def job_final_parser(context):
    log.info("[job] Финальный парсинг дня (23:55)")
    post_ids = await _run_parser()
    if post_ids is None:
        return

    assigned: dict[str, int] = {}
    open_count = 0
    for post_id in post_ids:
        tgid = assign_post(post_id)
        if tgid:
            assigned[tgid] = assigned.get(tgid, 0) + 1
        else:
            open_count += 1

    for tg_id, count in assigned.items():
        try:
            await context.bot.send_message(
                chat_id=tg_id,
                text=(
                    f"📬 Финальный сбор постов дня!\n\n"
                    f"🆕 Тебе добавлено: {count}\n\n"
                    f"/next — взять пост"
                ),
            )
        except Exception as e:
            log.warning(f"[scheduler] Не удалось уведомить {tg_id}: {e}")

    if open_count > 0:
        for tg_id in get_all_verified_ids():
            try:
                await context.bot.send_message(
                    chat_id=tg_id,
                    text=(
                        f"📬 Финальный сбор постов дня!\n\n"
                        f"🆕 Новых в очереди: {open_count}\n\n"
                        f"/next — взять пост"
                    ),
                )
            except Exception as e:
                log.warning(f"[scheduler] Не удалось уведомить {tg_id}: {e}")


async def job_new_day(context):
    log.info("[job] Смена дня")
    today = _create_new_day()
    log.info(f"[job] Создан новый день: {today}")


async def job_check_expired(context):
    """
    Переназначает посты которые назначены но не взяты за EXPIRE_MINUTES.
    Взятые посты (жюри нажал /next) таймаут не затрагивает.
    """
    released = release_expired_posts()
    if not released:
        return

    log.info(f"[expired] Событий: {len(released)}")

    already_notified: set[str] = set()
    reassigned: dict[str, int] = {}

    for item in released:
        if item["type"] != "reassigned":
            continue
        tgid = item["reviewer_tgid"]
        reassigned[tgid] = reassigned.get(tgid, 0) + 1

    for tgid, count in reassigned.items():
        already_notified.add(tgid)
        s = "ы" if count > 1 else ""
        try:
            await context.bot.send_message(
                chat_id=tgid,
                text=(
                    f"📬 Тебе назначен{s} {count} пост{s}!\n\n"
                    f"🔄 Переназначено из-за истечения времени у другого жюри.\n\n"
                    f"/next — взять пост"
                ),
            )
        except Exception as e:
            log.warning(f"[expired] Не удалось уведомить {tgid}: {e}")

    free_events = [r for r in released if r["type"] == "free"]
    if free_events:
        free_count = get_free_posts_count()
        for tgid in get_all_verified_ids():
            if tgid in already_notified:
                continue
            try:
                await context.bot.send_message(
                    chat_id=tgid,
                    text=(
                        f"📬 Появились свободные посты!\n\n"
                        f"📋 Доступно: {free_count}\n\n"
                        f"/next — взять пост"
                    ),
                )
            except Exception as e:
                log.warning(f"[expired] Не удалось уведомить {tgid}: {e}")
