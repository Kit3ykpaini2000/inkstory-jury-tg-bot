"""
bot/handlers/admin.py — хендлеры админ панели

Команды: /admin
"""

import os
import pathlib

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from utils.config import QUEUE_MODE
from utils.logger import setup_logger
from utils.db.jury import is_admin, get_all_reviewers, get_reviewer_stats, set_verified
from utils.db.posts import get_posts_stats
from parser.queue_manager import get_total_queue_count, get_all_reviewer_queue_sizes
from bot.keyboards import (
    admin_keyboard, back_keyboard, logs_keyboard,
    queue_mode_keyboard, verify_list_keyboard,
)

log      = setup_logger()
LOG_FILE = pathlib.Path(__file__).parent.parent.parent / "logs" / "app.log"

WAITING_SHUTDOWN_REASON = 100


# ── Хелперы ───────────────────────────────────────────────────────────────────

def _get_log_lines(n: int) -> str:
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        last = lines[-n:] if len(lines) >= n else lines
        return "".join(last).strip()
    except FileNotFoundError:
        return "Файл логов не найден."
    except Exception as e:
        return f"Ошибка чтения логов: {e}"


def _build_verify_text(reviewers: list[dict]) -> str:
    text = "👥 Жюри — выбери для верификации:\n\n"
    for r in reviewers:
        status = "✅" if r["verified"] else "⏳"
        admin  = " 👑" if r["is_admin"] else ""
        text  += f"{status} {r['name']}{admin} — проверено: {r['checked']}\n"
    return text[:4000]


# ── Команды ───────────────────────────────────────────────────────────────────

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    if not is_admin(tg_id):
        await update.message.reply_text("У тебя нет прав администратора.")
        return
    await update.message.reply_text(
        "🔧 Панель администратора:",
        reply_markup=admin_keyboard(),
    )


# ── Callbacks ─────────────────────────────────────────────────────────────────

async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_id = str(query.from_user.id)
    data  = query.data

    if not data.startswith(("admin_", "logs_", "qmode_")):
        return

    if not is_admin(tg_id):
        await query.answer()
        return

    await query.answer()

    # ── Статистика ────────────────────────────────────────────────────────────
    if data == "admin_stats":
        s = get_posts_stats()
        await query.edit_message_text(
            f"📊 Статистика:\n\n"
            f"📋 В очереди: {get_total_queue_count()}\n"
            f"✅ Проверено: {s['done']}\n"
            f"❌ Отклонено: {s['rejected']}\n"
            f"⚙️ Режим: {QUEUE_MODE}",
            reply_markup=back_keyboard(),
        )

    # ── Очередь ───────────────────────────────────────────────────────────────
    elif data == "admin_queue":
        rows  = get_all_reviewer_queue_sizes()
        total = sum(r["count"] for r in rows)
        if not rows:
            text = "📋 Нет верифицированных жюри."
        else:
            text = f"📋 Очереди (всего: {total}):\n\n"
            for r in rows:
                text += f"• {r['name']}: {r['count']} постов\n"
        await query.edit_message_text(text[:4000], reply_markup=back_keyboard())

    # ── Проверяющие ───────────────────────────────────────────────────────────
    elif data == "admin_reviewers":
        rows = get_reviewer_stats()
        if not rows:
            text = "👥 Проверяющих нет."
        else:
            text = "👥 Проверяющие:\n\n"
            for r in rows:
                text += f"• {r['Name']}: ✅ {r['checked']}  ❌ {r['rejected']}\n"
        await query.edit_message_text(text, reply_markup=back_keyboard())

    # ── Режим очереди ─────────────────────────────────────────────────────────
    elif data == "admin_queue_mode":
        await query.edit_message_text(
            f"⚙️ Режим очереди (текущий: {QUEUE_MODE}):",
            reply_markup=queue_mode_keyboard(QUEUE_MODE),
        )

    elif data in ("qmode_open", "qmode_balanced"):
        await query.edit_message_text(
            f"⚙️ Режим очереди нельзя изменить через бота.\n\n"
            f"Измени QUEUE_MODE в файле .env и перезапусти бота.\n\n"
            f"Текущий режим: {QUEUE_MODE}",
            reply_markup=back_keyboard(),
        )

    # ── Логи ──────────────────────────────────────────────────────────────────
    elif data == "admin_logs":
        await query.edit_message_text(
            "📄 Сколько строк показать?",
            reply_markup=logs_keyboard(),
        )

    elif data.startswith("logs_"):
        n    = int(data.split("_")[1])
        text = _get_log_lines(n)
        if len(text) > 3900:
            text = "...\n" + text[-3900:]
        await query.message.reply_text(
            f"📄 Последние {n} строк:\n\n<code>{text}</code>",
            parse_mode="HTML",
        )

    # ── Верификация ───────────────────────────────────────────────────────────
    elif data == "admin_verify":
        reviewers = get_all_reviewers()
        await query.edit_message_text(
            _build_verify_text(reviewers),
            reply_markup=verify_list_keyboard(reviewers),
        )

    elif data.startswith("admin_verify_"):
        target_id = data.replace("admin_verify_", "")
        set_verified(target_id, 1)
        log.info(f"[admin] Верифицирован {target_id}")
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="✅ Твоя учётная запись верифицирована!\n\nТеперь ты можешь получать посты через /next."
            )
        except Exception:
            pass
        await query.answer("✅ Верифицирован!")
        reviewers = get_all_reviewers()
        await query.edit_message_text(
            _build_verify_text(reviewers),
            reply_markup=verify_list_keyboard(reviewers),
        )

    elif data.startswith("admin_unverify_"):
        target_id = data.replace("admin_unverify_", "")
        set_verified(target_id, 0)
        log.info(f"[admin] Отозвана верификация {target_id}")
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="⏳ Твоя верификация отозвана администратором."
            )
        except Exception:
            pass
        await query.answer("❌ Верификация отозвана!")
        reviewers = get_all_reviewers()
        await query.edit_message_text(
            _build_verify_text(reviewers),
            reply_markup=verify_list_keyboard(reviewers),
        )

    # ── Назад ─────────────────────────────────────────────────────────────────
    elif data == "admin_back":
        await query.edit_message_text(
            "🔧 Панель администратора:",
            reply_markup=admin_keyboard(),
        )


# ── Выключение ────────────────────────────────────────────────────────────────

async def cmd_shutdown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if not is_admin(str(query.from_user.id)):
        return ConversationHandler.END
    await query.message.reply_text("🔴 Введи причину выключения бота:")
    return WAITING_SHUTDOWN_REASON


async def got_shutdown_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from utils.db.jury import get_all_verified_ids
    tg_id  = str(update.effective_user.id)
    reason = update.message.text.strip()

    if not is_admin(tg_id):
        return ConversationHandler.END

    log.info(f"[admin] Выключение бота. Причина: {reason}")

    text = (
        f"🔴 Бот выключается.\n\n"
        f"📋 Причина: {reason}\n\n"
        f"Бот будет недоступен до следующего запуска."
    )
    for tg in get_all_verified_ids():
        try:
            await context.bot.send_message(chat_id=tg, text=text)
        except Exception:
            pass

    await update.message.reply_text(f"✅ Уведомления отправлены. Выключаюсь...\nПричина: {reason}")
    log.info("[admin] Бот остановлен администратором")
    os.kill(os.getpid(), 15)
    return ConversationHandler.END
