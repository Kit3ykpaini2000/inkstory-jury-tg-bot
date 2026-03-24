"""
main.py — точка входа бота

Запуск: python main.py
"""

import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from datetime import time as dtime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(pathlib.Path(__file__).parent / ".env")

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

from utils.config import (
    BOT_TOKEN, PARSER_INTERVAL, EXPIRE_CHECK_INTERVAL,
    FINAL_PARSER_HOUR, FINAL_PARSER_MINUTE,
    NEW_DAY_HOUR, NEW_DAY_MINUTE,
    validate as validate_config,
)
from utils.logger import setup_logger
from utils.db_helpers import get_all_verified_ids, release_stuck_posts

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
from bot.scheduler import (
    job_auto_parser, job_final_parser, job_new_day, job_check_expired,
)

log       = setup_logger()
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


# ── Startup / Shutdown ────────────────────────────────────────────────────────

async def on_startup(app):
    log.info("Бот запущен")
    for tg_id in get_all_verified_ids():
        try:
            await app.bot.send_message(
                chat_id=tg_id, text="🟢 Бот запущен и готов к работе!"
            )
        except Exception:
            pass


async def on_shutdown(app):
    count = release_stuck_posts()
    if count:
        log.info(f"[shutdown] Сброшено зависших постов: {count}")
    log.info("Бот остановлен")


# ── Запуск ────────────────────────────────────────────────────────────────────

def run():
    try:
        validate_config()
    except ValueError as e:
        log.error(f".env ошибка: {e}")
        sys.exit(1)

    log.info("=" * 50)
    log.info("Запуск бота")
    log.info("=" * 50)

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

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
    jq.run_repeating(job_auto_parser,   interval=PARSER_INTERVAL,       first=60, name="auto_parser")
    jq.run_repeating(job_check_expired, interval=EXPIRE_CHECK_INTERVAL, first=60, name="check_expired")
    jq.run_daily(job_final_parser, time=dtime(hour=FINAL_PARSER_HOUR, minute=FINAL_PARSER_MINUTE, tzinfo=MOSCOW_TZ), name="final_parser")
    jq.run_daily(job_new_day,      time=dtime(hour=NEW_DAY_HOUR,      minute=NEW_DAY_MINUTE,      tzinfo=MOSCOW_TZ), name="new_day")

    log.info(
        f"[scheduler] Автопарсер каждые {PARSER_INTERVAL // 60} мин, "
        f"просроченные каждые {EXPIRE_CHECK_INTERVAL // 60} мин, "
        f"финальный {FINAL_PARSER_HOUR}:{FINAL_PARSER_MINUTE:02d}, "
        f"смена дня {NEW_DAY_HOUR}:{NEW_DAY_MINUTE:02d} МСК"
    )

    app.run_polling()


if __name__ == "__main__":
    run()
