"""
bot/handlers/admin.py — хендлеры админ панели

Команды: /admin
"""

import os
import sys
import pathlib

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from utils.database import get_db
from utils.config import QUEUE_MODE
from utils.logger import setup_logger
from parser.queue_manager import (
    get_total_queue_count, get_all_reviewer_queue_sizes, assign_post,
)
from bot.keyboards import (
    admin_keyboard, back_keyboard, logs_keyboard,
    queue_mode_keyboard, verify_list_keyboard,
)

log      = setup_logger()
LOG_FILE = pathlib.Path(__file__).parent.parent.parent / "logs" / "app.log"

WAITING_SHUTDOWN_REASON = 100


# ── Хелперы ───────────────────────────────────────────────────────────────────

def _is_admin(tg_id: str) -> bool:
    with get_db() as db:
        row = db.execute(
            "SELECT IsAdmin FROM reviewers WHERE TGID=?", (tg_id,)
        ).fetchone()
        return row is not None and row["IsAdmin"] == 1


def _get_stats() -> dict:
    with get_db() as db:
        checked  = db.execute(
            "SELECT COUNT(*) FROM posts_info WHERE Status='done'"
        ).fetchone()[0]
        rejected = db.execute(
            "SELECT COUNT(*) FROM posts_info WHERE Status='rejected'"
        ).fetchone()[0]
        reviewers = db.execute(
            "SELECT COUNT(*) FROM reviewers"
        ).fetchone()[0]
    return {
        "in_queue":  get_total_queue_count(),
        "checked":   checked,
        "rejected":  rejected,
        "reviewers": reviewers,
        "mode":      QUEUE_MODE,
    }


def _get_reviewer_stats() -> list:
    with get_db() as db:
        return db.execute(
            """
            SELECT
                rv.Name,
                COUNT(CASE WHEN r.HumanWords IS NOT NULL THEN 1 END) AS checked,
                COUNT(CASE WHEN p.Status='rejected' AND r.Reviewer=rv.TGID THEN 1 END) AS rejected
            FROM reviewers rv
            LEFT JOIN results    r ON r.Reviewer = rv.TGID
            LEFT JOIN posts_info p ON p.ID = r.Post
            GROUP BY rv.TGID
            ORDER BY checked DESC
            """
        ).fetchall()


def _get_all_reviewers() -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT TGID, Name, COALESCE(Verified,0) AS Verified,
                   COALESCE(IsAdmin,0) AS IsAdmin,
                   COUNT(CASE WHEN r.HumanWords IS NOT NULL THEN 1 END) AS checked
            FROM reviewers rv
            LEFT JOIN results r ON r.Reviewer = rv.TGID
            GROUP BY rv.TGID
            ORDER BY Verified ASC, Name ASC
            """
        ).fetchall()
    return [
        {
            "tgid":      r["TGID"],
            "name":      r["Name"],
            "verified":  bool(r["Verified"]),
            "is_admin":  bool(r["IsAdmin"]),
            "checked":   r["checked"],
        }
        for r in rows
    ]


def _set_verified(tg_id: str, value: int) -> None:
    with get_db() as db:
        db.execute(
            "UPDATE reviewers SET Verified=? WHERE TGID=?", (value, tg_id)
        )
        db.commit()


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
    if not _is_admin(tg_id):
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

    if not _is_admin(tg_id):
        await query.answer()
        return

    await query.answer()

    # ── Статистика ────────────────────────────────────────────────────────────
    if data == "admin_stats":
        s = _get_stats()
        await query.edit_message_text(
            f"📊 Статистика:\n\n"
            f"📋 В очереди: {s['in_queue']}\n"
            f"✅ Проверено: {s['checked']}\n"
            f"❌ Отклонено: {s['rejected']}\n"
            f"👥 Проверяющих: {s['reviewers']}\n"
            f"⚙️ Режим: {s['mode']}",
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
        rows = _get_reviewer_stats()
        if not rows:
            text = "👥 Проверяющих нет."
        else:
            text = "👥 Проверяющие:\n\n"
            for r in rows:
                text += f"• {r['Name']}: ✅ {r['checked']}  ❌ {r['rejected']}\n"
        await query.edit_message_text(text, reply_markup=back_keyboard())

    # ── Режим очереди ─────────────────────────────────────────────────────────
    elif data == "admin_queue_mode":
        mode = QUEUE_MODE
        await query.edit_message_text(
            f"⚙️ Режим очереди (текущий: {mode}):",
            reply_markup=queue_mode_keyboard(mode),
        )

    elif data in ("qmode_open", "qmode_distributed", "qmode_balanced"):
        new_mode = data.replace("qmode_", "")
        await query.edit_message_text(
            f"⚙️ Режим очереди нельзя изменить через бота.\n\nИзмени QUEUE_MODE в файле .env и перезапусти бота.\n\nТекущий режим: {QUEUE_MODE}",
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
        reviewers = _get_all_reviewers()
        await query.edit_message_text(
            _build_verify_text(reviewers),
            reply_markup=verify_list_keyboard(reviewers),
        )

    elif data.startswith("admin_verify_"):
        target_id = data.replace("admin_verify_", "")
        _set_verified(target_id, 1)
        # Назначаем ему посты если режим distributed
        if QUEUE_MODE == "distributed":
            with get_db() as db:
                free = db.execute(
                    "SELECT Post FROM queue WHERE Reviewer IS NULL LIMIT 10"
                ).fetchall()
            for row in free:
                assign_post(row["Post"])
        log.info(f"[admin] Верифицирован {target_id}")
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="✅ Твоя учётная запись верифицирована!\n\nТеперь ты можешь получать посты через /next."
            )
        except Exception:
            pass
        await query.answer("✅ Верифицирован!")
        reviewers = _get_all_reviewers()
        await query.edit_message_text(
            _build_verify_text(reviewers),
            reply_markup=verify_list_keyboard(reviewers),
        )

    elif data.startswith("admin_unverify_"):
        target_id = data.replace("admin_unverify_", "")
        _set_verified(target_id, 0)
        log.info(f"[admin] Отозвана верификация {target_id}")
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="⏳ Твоя верификация отозвана администратором."
            )
        except Exception:
            pass
        await query.answer("❌ Верификация отозвана!")
        reviewers = _get_all_reviewers()
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
    if not _is_admin(str(query.from_user.id)):
        return ConversationHandler.END
    await query.message.reply_text(
        "🔴 Введи причину выключения бота:"
    )
    return WAITING_SHUTDOWN_REASON


async def got_shutdown_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    tg_id  = str(update.effective_user.id)
    reason = update.message.text.strip()

    if not _is_admin(tg_id):
        return ConversationHandler.END

    log.info(f"[admin] Выключение бота. Причина: {reason}")

    with get_db() as db:
        rows = db.execute(
            "SELECT TGID FROM reviewers WHERE Verified=1"
        ).fetchall()

    text = (
        f"🔴 Бот выключается.\n\n"
        f"📋 Причина: {reason}\n\n"
        f"Бот будет недоступен до следующего запуска."
    )
    for row in rows:
        try:
            await context.bot.send_message(chat_id=row["TGID"], text=text)
        except Exception:
            pass

    await update.message.reply_text(f"✅ Уведомления отправлены. Выключаюсь...\nПричина: {reason}")
    log.info("[admin] Бот остановлен администратором")
    os.kill(os.getpid(), 15)
    return ConversationHandler.END
