"""
bot.py — запуск бота, регистрация хендлеров, планировщик задач

Автоматические задачи:
- Каждые N минут (из .env) — запуск парсера, уведомление жюри при новых постах
- 23:55 — запуск парсера (финальный сбор постов дня)
- 00:01 — создание нового дня в таблице days
"""

import sys
import asyncio
import pathlib
import functools
from datetime import datetime, time as dtime

ROOT = pathlib.Path(__file__).parent

from dotenv import load_dotenv
import os

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

load_dotenv(ROOT / ".env")

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
from bot.handlers.admin import cmd_admin, cb_admin, cmd_shutdown, got_shutdown_reason, WAITING_SHUTDOWN_REASON

log = setup_logger()

BOT_TOKEN       = os.getenv("BOT_TOKEN")
PARSER_INTERVAL = int(os.getenv("PARSER_INTERVAL", 30)) * 60  # минуты → секунды

_parser_lock = asyncio.Lock()  # не запускать парсер дважды одновременно


# ── Хелперы БД ────────────────────────────────────────────────────────────────

def _get_all_reviewer_ids() -> list[str]:
    with get_db() as db:
        rows = db.execute("SELECT TGID FROM reviewers WHERE Verified = 1").fetchall()
        return [row["TGID"] for row in rows]


def _get_queue_count() -> int:
    from parser.queue_manager import get_total_queue_count
    return get_total_queue_count()


def _create_new_day() -> str:
    """Создаёт новую запись в таблице days с сегодняшней датой."""
    today = datetime.now().strftime("%d.%m.%Y")
    with get_db() as db:
        db.execute("INSERT INTO days (Data) VALUES (?)", (today,))
        db.commit()
    return today


# ── Запуск парсера ────────────────────────────────────────────────────────────

def _run_parser_sync() -> dict:
    """
    Синхронная обёртка для запуска парсера.
    Вызывается в отдельном потоке через run_in_executor.
    """
    from parser.links import parse as parse_links
    from parser.posts import parse as parse_posts

    parse_links()
    return parse_posts()


async def _run_parser() -> dict | None:
    """
    Запускает парсер в отдельном потоке через run_in_executor.
    Не блокирует event loop бота во время парсинга.
    Возвращает dict {tgid: count} или None при ошибке / если уже запущен.
    """
    if _parser_lock.locked():
        log.warning("[parser] Уже запущен, пропускаем")
        return None

    async with _parser_lock:
        try:
            log.info("[parser] Запуск в executor...")
            loop = asyncio.get_running_loop()
            assigned = await loop.run_in_executor(
                None,
                functools.partial(_run_parser_sync),
            )
            log.info(f"[parser] Готово. В очереди: {_get_queue_count()}")
            return assigned if isinstance(assigned, dict) else {}

        except Exception as e:
            log.exception(f"[parser] Исключение: {e}")
            return None


# ── Задачи планировщика ───────────────────────────────────────────────────────

async def job_auto_parser(context):
    """Автозапуск парсера по расписанию. Уведомляет жюри если появились новые посты."""
    log.info("[job] Автозапуск парсера")
    assigned = await _run_parser()

    if not assigned:
        return

    sent = 0
    for tg_id, count in assigned.items():
        try:
            text = (
                f"📬 Появились новые посты!\n\n"
                f"🆕 Тебе добавлено: {count}\n\n"
                f"/next — взять пост"
            )
            await context.bot.send_message(chat_id=tg_id, text=text)
            sent += 1
        except Exception as e:
            log.warning(f"[job] Не удалось уведомить {tg_id}: {e}")

    log.info(f"[job] Уведомлено жюри: {sent}/{len(assigned)}")


async def job_final_parser(context):
    """23:55 — финальный запуск парсера перед сменой дня."""
    log.info("[job] Финальный парсинг дня (23:55)")
    assigned = await _run_parser()

    if assigned:
        for tg_id, count in assigned.items():
            try:
                text = (
                    f"📬 Финальный сбор постов дня!\n\n"
                    f"🆕 Тебе добавлено: {count}\n\n"
                    f"/next — взять пост"
                )
                await context.bot.send_message(chat_id=tg_id, text=text)
            except Exception as e:
                log.warning(f"[job] Не удалось уведомить {tg_id}: {e}")


async def job_new_day(context):
    """00:01 — создаёт новый день в таблице days."""
    log.info("[job] Смена дня")
    today = _create_new_day()
    log.info(f"[job] Создан новый день: {today}")


# ── Запуск бота ───────────────────────────────────────────────────────────────

def run():
    if not BOT_TOKEN:
        log.error("BOT_TOKEN не найден в .env")
        sys.exit(1)

    log.info("=" * 50)
    log.info("Бот запущен")
    log.info("=" * 50)

    async def on_startup(app):
        reviewer_ids = _get_all_reviewer_ids()
        for tg_id in reviewer_ids:
            try:
                await app.bot.send_message(chat_id=tg_id, text="🟢 Бот запущен и готов к работе!")
            except Exception:
                pass
        log.info(f"[startup] Уведомлено жюри: {len(reviewer_ids)}")

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(on_startup).build()

    # ── Хендлер регистрации ───────────────────────────────────────────────────
    reg_handler = ConversationHandler(
        entry_points=[CommandHandler("register", cmd_register)],
        states={
            WAITING_REG_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_reg_url)
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    # ── Хендлер проверки поста ────────────────────────────────────────────────
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

    # ── Хендлер выключения ────────────────────────────────────────────────────
    shutdown_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(cmd_shutdown, pattern="^admin_shutdown$")],
        states={
            WAITING_SHUTDOWN_REASON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_shutdown_reason),
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

    jq.run_repeating(
        job_auto_parser,
        interval=PARSER_INTERVAL,
        first=60,
        name="auto_parser",
    )

    jq.run_daily(
        job_final_parser,
        time=dtime(hour=23, minute=55),
        name="final_parser",
    )

    jq.run_daily(
        job_new_day,
        time=dtime(hour=0, minute=1),
        name="new_day",
    )

    log.info(f"[scheduler] Автопарсер каждые {PARSER_INTERVAL // 60} мин, финальный в 23:55, смена дня в 00:01")

    app.run_polling()


if __name__ == "__main__":
    run()
