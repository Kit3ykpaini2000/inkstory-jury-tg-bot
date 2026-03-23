"""
main.py — точка входа: бот + FastAPI + туннель + планировщик

Запуск: python main.py
"""

import os
import sys
import time
import threading
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from datetime import time as dtime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(pathlib.Path(__file__).parent / ".env")

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

from utils.config import (
    BOT_TOKEN, PARSER_INTERVAL, EXPIRE_CHECK_INTERVAL,
    FINAL_PARSER_HOUR, FINAL_PARSER_MINUTE,
    NEW_DAY_HOUR, NEW_DAY_MINUTE,
    TUNNEL_PROVIDER,
    validate as validate_config,
)
from utils.logger import setup_logger
from utils.db.jury import get_all_verified_ids
from utils.db.posts import release_stuck_posts

from bot.handlers.common import (
    cmd_start, cmd_register, cmd_stats, cmd_fullstats,
    got_reg_url, WAITING_REG_URL,
)
from bot.handlers.review import (
    cmd_next, cmd_cancel, got_words, got_errors,
    got_skip_text, got_reject_custom,
    cb_skip_post, cb_skip_cancel, cb_reject_post, cb_reject_reason, cb_ai_check,
    WAITING_WORDS, WAITING_ERRORS,
    WAITING_SKIP_TEXT, WAITING_REJECT_REASON, WAITING_REJECT_CUSTOM,
)
from bot.handlers.admin import (
    cmd_admin, cb_admin, cmd_shutdown, got_shutdown_reason,
    WAITING_SHUTDOWN_REASON,
)
from bot.scheduler import (
    job_auto_parser, job_final_parser, job_new_day, job_check_expired,
)
from tunnel import TunnelManager

log       = setup_logger()
MOSCOW_TZ = ZoneInfo("Europe/Moscow")

# Глобальный экземпляр — используется в admin.py при верификации
tunnel_manager = TunnelManager(
    bot_token=BOT_TOKEN,
    provider=TUNNEL_PROVIDER,
    port=8000,
    retries=3,
    retry_delay=10,
    get_tg_ids=get_all_verified_ids,
)


# ── FastAPI ───────────────────────────────────────────────────────────────────

def _run_api() -> None:
    import uvicorn
    from api.app import app as fastapi_app
    uvicorn.run(fastapi_app, host="0.0.0.0", port=8000, log_level="warning")


# ── Туннель ───────────────────────────────────────────────────────────────────

def _start_tunnel_bg(app) -> None:
    """Запускает туннель в фоне и обновляет кнопку меню."""
    url = tunnel_manager.start()
    if url:
        tunnel_manager.update_menu_button()
        os.environ["_TUNNEL_URL"] = url
        app.bot_data["tunnel_url"] = url


# ── Команды туннеля (только для админов) ─────────────────────────────────────

async def cmd_tunnel_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает текущий URL и обновляет кнопку меню у всех жюри."""
    from utils.db.jury import is_admin
    if not is_admin(str(update.effective_user.id)):
        return
    if not tunnel_manager.url:
        await update.message.reply_text("❌ Туннель не запущен.")
        return
    ok = tunnel_manager.update_menu_button()
    status = "✅" if ok else "⚠️ Частично"
    await update.message.reply_text(f"{status} Mini App URL обновлён:\n{tunnel_manager.url}")


async def cmd_tunnel_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Перезапускает туннель и обновляет кнопку меню."""
    from utils.db.jury import is_admin
    if not is_admin(str(update.effective_user.id)):
        return
    await update.message.reply_text("🔄 Перезапускаю туннель...")

    import asyncio
    app = context.application
    url = await asyncio.get_running_loop().run_in_executor(None, tunnel_manager.start)
    if url:
        tunnel_manager.update_menu_button()
        app.bot_data["tunnel_url"] = url
        await update.message.reply_text(f"✅ Туннель запущен:\n{url}")
    else:
        await update.message.reply_text("❌ Не удалось запустить туннель.")


# ── Startup / Shutdown ────────────────────────────────────────────────────────

async def on_startup(app) -> None:
    log.info("Бот запущен")
    for tg_id in get_all_verified_ids():
        try:
            await app.bot.send_message(chat_id=tg_id, text="🟢 Бот запущен и готов к работе!")
        except Exception:
            pass


async def on_shutdown(app) -> None:
    count = release_stuck_posts()
    if count:
        log.info(f"[shutdown] Сброшено зависших постов: {count}")
    log.info("Бот остановлен")


# ── Запуск ────────────────────────────────────────────────────────────────────

def run() -> None:
    try:
        validate_config()
    except ValueError as e:
        log.error(f".env ошибка: {e}")
        sys.exit(1)

    log.info("=" * 50)
    log.info(f"Запуск бота + API [{TUNNEL_PROVIDER}]")
    log.info("=" * 50)

    threading.Thread(target=_run_api, daemon=True, name="fastapi").start()
    log.info("[api] FastAPI запущен на http://0.0.0.0:8000")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    # Туннель стартует через 2 сек — даём FastAPI подняться
    time.sleep(2)
    threading.Thread(
        target=_start_tunnel_bg, args=(app,), daemon=True, name="tunnel"
    ).start()

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
    app.add_handler(CommandHandler("url",       cmd_tunnel_url))
    app.add_handler(CommandHandler("tunnel",    cmd_tunnel_restart))
    app.add_handler(CallbackQueryHandler(cb_admin))

    # ── Планировщик ──────────────────────────────────────────────────────────

    jq = app.job_queue
    jq.run_repeating(job_auto_parser,   interval=PARSER_INTERVAL,       first=60, name="auto_parser")
    jq.run_repeating(job_check_expired, interval=EXPIRE_CHECK_INTERVAL, first=60, name="check_expired")
    jq.run_daily(
        job_final_parser,
        time=dtime(hour=FINAL_PARSER_HOUR, minute=FINAL_PARSER_MINUTE, tzinfo=MOSCOW_TZ),
        name="final_parser",
    )
    jq.run_daily(
        job_new_day,
        time=dtime(hour=NEW_DAY_HOUR, minute=NEW_DAY_MINUTE, tzinfo=MOSCOW_TZ),
        name="new_day",
    )

    log.info(
        f"[scheduler] Автопарсер каждые {PARSER_INTERVAL // 60} мин, "
        f"просроченные каждые {EXPIRE_CHECK_INTERVAL // 60} мин, "
        f"финальный {FINAL_PARSER_HOUR}:{FINAL_PARSER_MINUTE:02d}, "
        f"смена дня {NEW_DAY_HOUR}:{NEW_DAY_MINUTE:02d} МСК"
    )

    app.run_polling()


if __name__ == "__main__":
    run()
