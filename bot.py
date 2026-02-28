"""
bot.py — запуск бота, регистрация хендлеров, планировщик задач

Автоматические задачи:
- Каждые N минут — парсер + уведомление жюри
- Каждые 5 минут — освобождение просроченных постов
- 23:55 МСК — финальный парсинг
- 00:01 МСК — создание нового дня
"""

import sys
import asyncio
import pathlib
import functools
import os
from datetime import datetime, time as dtime
from pytz import timezone

ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

from utils.database import get_db
from utils.logger import setup_logger
from bot.handlers.user import (
    cmd_start, cmd_register, cmd_next, cmd_cancel, cmd_stats, cmd_fullstats,
    got_reg_url, got_words, got_errors,
    got_skip_text, got_reject_custom,
    cb_skip_post, cb_skip_cancel, cb_reject_post, cb_reject_reason, cb_ai_check,
    WAITING_REG_URL, WAITING_WORDS, WAITING_ERRORS,
    WAITING_SKIP_TEXT, WAITING_REJECT_REASON, WAITING_REJECT_CUSTOM,
)
from bot.handlers.admin import (
    cmd_admin, cb_admin, cmd_shutdown, got_shutdown_reason,
    WAITING_SHUTDOWN_REASON,
)

log = setup_logger()

from utils.config import (
    BOT_TOKEN, PARSER_INTERVAL, QUEUE_MODE,
    EXPIRE_CHECK_INTERVAL,
    FINAL_PARSER_HOUR, FINAL_PARSER_MINUTE,
    NEW_DAY_HOUR, NEW_DAY_MINUTE,
    validate as validate_config,
)
MOSCOW_TZ = timezone("Europe/Moscow")

_parser_lock = asyncio.Lock()


# ── Хелперы ───────────────────────────────────────────────────────────────────

def _get_all_reviewer_ids() -> list[str]:
    with get_db() as db:
        rows = db.execute(
            "SELECT TGID FROM reviewers WHERE Verified=1"
        ).fetchall()
        return [r["TGID"] for r in rows]


def _create_new_day() -> str:
    today = datetime.now(MOSCOW_TZ).strftime("%d.%m.%Y")
    with get_db() as db:
        db.execute("INSERT INTO days (Data) VALUES (?)", (today,))
        db.commit()
    return today


# ── Парсер ────────────────────────────────────────────────────────────────────

def _run_parser_sync() -> dict:
    from parser.links import parse as parse_links
    from parser.posts import parse as parse_posts
    parse_links()
    return parse_posts()


async def _run_parser() -> dict | None:
    if _parser_lock.locked():
        log.warning("[parser] Уже запущен, пропускаем")
        return None

    async with _parser_lock:
        try:
            loop    = asyncio.get_running_loop()
            assigned = await loop.run_in_executor(None, functools.partial(_run_parser_sync))
            log.info(f"[parser] Готово. Назначено постов: {sum(assigned.values()) if assigned else 0}")
            return assigned if isinstance(assigned, dict) else {}
        except Exception as e:
            log.exception(f"[parser] Исключение: {e}")
            return None


# ── Задачи планировщика ───────────────────────────────────────────────────────

async def job_auto_parser(context):
    log.info("[job] Автозапуск парсера")
    assigned = await _run_parser()
    if not assigned:
        return

    for tg_id, count in assigned.items():
        try:
            await context.bot.send_message(
                chat_id=tg_id,
                text=(
                    f"📬 Появились новые посты!\n\n"
                    f"🆕 Тебе добавлено: {count}\n\n"
                    f"/next — взять пост"
                ),
            )
        except Exception as e:
            log.warning(f"[job] Не удалось уведомить {tg_id}: {e}")


async def job_final_parser(context):
    log.info("[job] Финальный парсинг дня (23:55)")
    assigned = await _run_parser()
    if not assigned:
        return

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
            log.warning(f"[job] Не удалось уведомить {tg_id}: {e}")


async def job_new_day(context):
    log.info("[job] Смена дня")
    today = _create_new_day()
    log.info(f"[job] Создан новый день: {today}")


async def job_check_expired(context):
    """
    Каждые 5 минут освобождает просроченные посты и уведомляет жюри.
    """
    from parser.queue_manager import release_expired_posts, get_free_posts_count

    released = release_expired_posts()
    if not released:
        return

    log.info(f"[expired] Освобождено: {len(released)}")

    # Уведомляем жюри у которых забрали посты
    notified = set()
    for item in released:
        tgid = item["reviewer_tgid"]
        notified.add(tgid)
        try:
            await context.bot.send_message(
                chat_id=tgid,
                text=(
                    "⏰ У тебя истекло время на проверку!\n\n"
                    "Пост возвращён в общую очередь.\n\n"
                    "Если хочешь продолжить — используй /next."
                ),
            )
        except Exception as e:
            log.warning(f"[expired] Не удалось уведомить {tgid}: {e}")

    # Уведомляем остальных жюри о свободных постах
    free_count = get_free_posts_count()
    if free_count > 0:
        for tgid in _get_all_reviewer_ids():
            if tgid in notified:
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


# ── Запуск ────────────────────────────────────────────────────────────────────

def run():
    try:
        validate_config()
    except ValueError as e:
        log.error(f".env ошибка: {e}")
        sys.exit(1)

    log.info("=" * 50)
    log.info("Бот запущен")
    log.info("=" * 50)

    async def on_startup(app):
        for tg_id in _get_all_reviewer_ids():
            try:
                await app.bot.send_message(
                    chat_id=tg_id, text="🟢 Бот запущен и готов к работе!"
                )
            except Exception:
                pass

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(on_startup).build()

    # ── Хендлеры ─────────────────────────────────────────────────────────────
    reg_handler = ConversationHandler(
        entry_points=[CommandHandler("register", cmd_register)],
        states={
            WAITING_REG_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_reg_url)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    check_handler = ConversationHandler(
        entry_points=[CommandHandler("next", cmd_next)],
        per_message=False,
        states={
            WAITING_WORDS: [
                CommandHandler("cancel", cmd_cancel),
                CommandHandler("next",   cmd_next),
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_words),
                CallbackQueryHandler(cb_skip_post,   pattern="^skip_post$"),
                CallbackQueryHandler(cb_reject_post, pattern="^reject_post$"),
                CallbackQueryHandler(cb_ai_check,    pattern="^ai_check$"),
            ],
            WAITING_ERRORS: [
                CommandHandler("cancel", cmd_cancel),
                CommandHandler("next",   cmd_next),
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_errors),
            ],
            WAITING_SKIP_TEXT: [
                CommandHandler("cancel", cmd_cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_skip_text),
                CallbackQueryHandler(cb_skip_cancel, pattern="^skip_cancel$"),
            ],
            WAITING_REJECT_REASON: [
                CommandHandler("cancel", cmd_cancel),
                CallbackQueryHandler(cb_reject_reason, pattern="^reject_"),
            ],
            WAITING_REJECT_CUSTOM: [
                CommandHandler("cancel", cmd_cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_reject_custom),
                CallbackQueryHandler(cb_reject_reason, pattern="^reject_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    shutdown_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(cmd_shutdown, pattern="^admin_shutdown$")],
        states={
            WAITING_SHUTDOWN_REASON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_shutdown_reason)
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )

    app.add_handler(reg_handler)
    app.add_handler(check_handler)
    app.add_handler(shutdown_handler)
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("stats",     cmd_stats))
    app.add_handler(CommandHandler("fullstats", cmd_fullstats))
    app.add_handler(CommandHandler("admin",     cmd_admin))
    app.add_handler(CallbackQueryHandler(cb_admin))

    # ── Планировщик ───────────────────────────────────────────────────────────
    jq = app.job_queue
    jq.run_repeating(job_auto_parser,    interval=PARSER_INTERVAL, first=60,    name="auto_parser")
    jq.run_repeating(job_check_expired,  interval=EXPIRE_CHECK_INTERVAL, first=60, name="check_expired")
    jq.run_daily(job_final_parser, time=dtime(hour=FINAL_PARSER_HOUR, minute=FINAL_PARSER_MINUTE, tzinfo=MOSCOW_TZ), name="final_parser")
    jq.run_daily(job_new_day,      time=dtime(hour=NEW_DAY_HOUR,      minute=NEW_DAY_MINUTE,      tzinfo=MOSCOW_TZ), name="new_day")

    log.info(
        f"[scheduler] Автопарсер каждые {PARSER_INTERVAL // 60} мин, "
        f"просроченные каждые 5 мин, финальный 23:55, смена дня 00:01 МСК"
    )

    app.run_polling()


if __name__ == "__main__":
    run()
