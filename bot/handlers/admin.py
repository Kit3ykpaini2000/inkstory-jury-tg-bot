"""
admin.py — хендлеры админ панели

Команды: /admin
Кнопки: Статистика, Очередь, Проверяющие, Логи
"""

import os
import sys
import pathlib
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from utils.database import get_db
from parser.queue_manager import create_queue, get_queue_count, get_total_queue_count
from utils.logger import setup_logger
from bot.keyboards import admin_keyboard, logs_keyboard, back_keyboard, verify_keyboard

log = setup_logger()

WAITING_SHUTDOWN_REASON = 100

LOG_FILE = pathlib.Path(__file__).parent.parent.parent / "logs" / "app.log"


# ── Хелперы БД ────────────────────────────────────────────────────────────────

def _is_admin(tg_id: str) -> bool:
    with get_db() as db:
        row = db.execute(
            "SELECT IsAdmin FROM reviewers WHERE TGID = ?", (tg_id,)
        ).fetchone()
        return row is not None and row["IsAdmin"] == 1


def _get_stats() -> dict:
    with get_db() as db:
        checked = db.execute(
            "SELECT COUNT(*) FROM posts_info WHERE HumanChecked = 1 AND Rejected = 0"
        ).fetchone()[0]
        rejected = db.execute(
            "SELECT COUNT(*) FROM posts_info WHERE Rejected = 1"
        ).fetchone()[0]
        reviewers = db.execute(
            "SELECT COUNT(*) FROM reviewers"
        ).fetchone()[0]
    in_queue = get_total_queue_count()
    return {
        "in_queue":  in_queue,
        "checked":   checked,
        "rejected":  rejected,
        "reviewers": reviewers,
    }


def _get_queue(limit: int = 20) -> list:
    """Возвращает список персональных очередей каждого жюри."""
    with get_db() as db:
        reviewers = db.execute(
            "SELECT TGID, Name FROM reviewers WHERE Verified = 1"
        ).fetchall()
    result = []
    for rv in reviewers:
        count = get_queue_count(rv["TGID"])
        result.append({"Name": rv["Name"], "TGID": rv["TGID"], "count": count})
    return result


def _get_reviewers() -> list:
    with get_db() as db:
        return db.execute(
            """
            SELECT
                rv.Name,
                COUNT(CASE WHEN r.HumanWords IS NOT NULL THEN 1 END) as checked,
                COUNT(CASE WHEN p.Rejected = 1 AND r.Reviewer = rv.TGID THEN 1 END) as rejected
            FROM reviewers rv
            LEFT JOIN results    r ON r.Reviewer = rv.TGID
            LEFT JOIN posts_info p ON p.ID = r.Post
            GROUP BY rv.TGID
            ORDER BY checked DESC
            """,
        ).fetchall()


def _get_all_reviewers_with_status() -> list:
    with get_db() as db:
        return db.execute(
            """
            SELECT TGID, Name, URL,
                   COALESCE(Verified, 0) as Verified,
                   COALESCE(IsAdmin, 0)  as IsAdmin,
                   COUNT(CASE WHEN r.HumanWords IS NOT NULL THEN 1 END) as checked
            FROM reviewers rv
            LEFT JOIN results r ON r.Reviewer = rv.TGID
            GROUP BY rv.TGID
            ORDER BY Verified ASC, Name ASC
            """
        ).fetchall()


def _set_verified(tg_id: str, value: int) -> None:
    with get_db() as db:
        db.execute("UPDATE reviewers SET Verified = ? WHERE TGID = ?", (value, tg_id))
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


# ── Команды ───────────────────────────────────────────────────────────────────

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    name  = update.effective_user.username or update.effective_user.first_name

    if not _is_admin(tg_id):
        log.warning(f"[admin] Нет прав: {name} ({tg_id})")
        await update.message.reply_text("У тебя нет прав администратора.")
        return

    log.info(f"[admin] {name} открыл панель")
    await update.message.reply_text(
        "🔧 Панель администратора:",
        reply_markup=admin_keyboard(),
    )


# ── Callbacks ─────────────────────────────────────────────────────────────────

async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_id = str(query.from_user.id)
    name  = query.from_user.username or query.from_user.first_name

    # Пропускаем callback-и жюри
    if not query.data.startswith("admin_") and not query.data.startswith("logs_"):
        return

    if not _is_admin(tg_id):
        await query.answer()
        return

    await query.answer()

    if query.data == "admin_stats":
        s = _get_stats()
        log.info(f"[admin:{name}] Статистика")
        await query.edit_message_text(
            f"📊 Статистика:\n\n"
            f"📋 В очереди: {s['in_queue']}\n"
            f"✅ Проверено: {s['checked']}\n"
            f"❌ Отклонено: {s['rejected']}\n"
            f"👥 Проверяющих: {s['reviewers']}",
            reply_markup=back_keyboard(),
        )

    elif query.data == "admin_queue":
        log.info(f"[admin:{name}] Очередь")
        rows = _get_queue()
        if not rows:
            text = "📋 Нет верифицированных жюри."
        else:
            total = sum(r["count"] for r in rows)
            text  = f"📋 Персональные очереди (всего: {total}):\n\n"
            for r in rows:
                text += f"• {r['Name']}: {r['count']} постов\n"
        await query.edit_message_text(text[:4000], reply_markup=back_keyboard())

    elif query.data == "admin_reviewers":
        log.info(f"[admin:{name}] Проверяющие")
        rows = _get_reviewers()
        if not rows:
            text = "👥 Проверяющих нет."
        else:
            text = "👥 Проверяющие:\n\n"
            for r in rows:
                text += f"• {r['Name']}: ✅ {r['checked']}  ❌ {r['rejected']}\n"
        await query.edit_message_text(text, reply_markup=back_keyboard())

    elif query.data == "admin_logs":
        log.info(f"[admin:{name}] Логи")
        await query.edit_message_text(
            "📄 Сколько строк показать?",
            reply_markup=logs_keyboard(),
        )

    elif query.data.startswith("logs_"):
        n = int(query.data.split("_")[1])
        log.info(f"[admin:{name}] Логи ({n} строк)")
        text = _get_log_lines(n)
        if len(text) > 3900:
            text = "...\n" + text[-3900:]
        await query.message.reply_text(
            f"📄 Последние {n} строк:\n\n<code>{text}</code>",
            parse_mode="HTML",
        )

    elif query.data == "admin_verify":
        log.info(f"[admin:{name}] Верификация")
        rows = _get_all_reviewers_with_status()
        if not rows:
            text = "👥 Проверяющих нет."
            await query.edit_message_text(text, reply_markup=back_keyboard())
            return

        text = "👥 Жюри — выбери для верификации:\n\n"
        buttons = []
        for r in rows:
            status = "✅" if r["Verified"] else "⏳"
            admin  = " 👑" if r["IsAdmin"] else ""
            text  += f"{status} {r['Name']}{admin} — проверено: {r['checked']}\n"
            buttons.append([InlineKeyboardButton(
                f"{status} {r['Name']}{admin}",
                callback_data=f"admin_verify_{r['TGID']}" if not r["Verified"] else f"admin_unverify_{r['TGID']}"
            )])
        buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_back")])
        await query.edit_message_text(text[:4000], reply_markup=InlineKeyboardMarkup(buttons))

    elif query.data.startswith("admin_verify_"):
        target_id = query.data.replace("admin_verify_", "")
        _set_verified(target_id, 1)
        create_queue(target_id)
        log.info(f"[admin:{name}] Верифицировал {target_id}")

        # Уведомляем жюри
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="✅ Твоя учётная запись верифицирована!\n\nТеперь ты можешь получать посты через /next."
            )
        except Exception:
            pass

        await query.answer("✅ Верифицирован!")
        # Обновляем список
        query.data = "admin_verify"
        rows = _get_all_reviewers_with_status()
        text = "👥 Жюри — выбери для верификации:\n\n"
        buttons = []
        for r in rows:
            status = "✅" if r["Verified"] else "⏳"
            admin  = " 👑" if r["IsAdmin"] else ""
            text  += f"{status} {r['Name']}{admin} — проверено: {r['checked']}\n"
            buttons.append([InlineKeyboardButton(
                f"{status} {r['Name']}{admin}",
                callback_data=f"admin_verify_{r['TGID']}" if not r["Verified"] else f"admin_unverify_{r['TGID']}"
            )])
        buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_back")])
        await query.edit_message_text(text[:4000], reply_markup=InlineKeyboardMarkup(buttons))

    elif query.data.startswith("admin_unverify_"):
        target_id = query.data.replace("admin_unverify_", "")
        _set_verified(target_id, 0)
        log.info(f"[admin:{name}] Отозвал верификацию {target_id}")

        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="⏳ Твоя верификация отозвана администратором.\nОбратись к администратору за подробностями."
            )
        except Exception:
            pass

        await query.answer("❌ Верификация отозвана!")
        rows = _get_all_reviewers_with_status()
        text = "👥 Жюри — выбери для верификации:\n\n"
        buttons = []
        for r in rows:
            status = "✅" if r["Verified"] else "⏳"
            admin  = " 👑" if r["IsAdmin"] else ""
            text  += f"{status} {r['Name']}{admin} — проверено: {r['checked']}\n"
            buttons.append([InlineKeyboardButton(
                f"{status} {r['Name']}{admin}",
                callback_data=f"admin_verify_{r['TGID']}" if not r["Verified"] else f"admin_unverify_{r['TGID']}"
            )])
        buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_back")])
        await query.edit_message_text(text[:4000], reply_markup=InlineKeyboardMarkup(buttons))

    elif query.data == "admin_back":
        await query.edit_message_text(
            "🔧 Панель администратора:",
            reply_markup=admin_keyboard(),
        )

# ── Выключение бота ───────────────────────────────────────────────────────────

async def cmd_shutdown(update: Update, context) -> int:
    query = update.callback_query
    tg_id = str(query.from_user.id)
    await query.answer()

    if not _is_admin(tg_id):
        return ConversationHandler.END

    await query.message.reply_text(
        "🔴 Введи причину выключения бота:\n"
        "(например: обновление, техобслуживание)"
    )
    return WAITING_SHUTDOWN_REASON


async def got_shutdown_reason(update: Update, context) -> int:
    tg_id  = str(update.effective_user.id)
    name   = update.effective_user.username or update.effective_user.first_name
    reason = update.message.text.strip()

    if not _is_admin(tg_id):
        return ConversationHandler.END

    log.info(f"[admin:{name}] Выключение бота. Причина: {reason}")

    # Уведомляем всех верифицированных жюри
    with get_db() as db:
        rows = db.execute("SELECT TGID FROM reviewers WHERE Verified = 1").fetchall()
    reviewer_ids = [r["TGID"] for r in rows]

    text = (
        f"🔴 Бот выключается.\n\n"
        f"📋 Причина: {reason}\n\n"
        f"Бот будет недоступен до следующего запуска."
    )

    for rid in reviewer_ids:
        try:
            await context.bot.send_message(chat_id=rid, text=text)
        except Exception:
            pass

    await update.message.reply_text(f"✅ Уведомления отправлены. Выключаюсь...\nПричина: {reason}")
    log.info("[admin] Бот остановлен администратором")
    os.kill(os.getpid(), 15)  # SIGTERM — корректное завершение
    return ConversationHandler.END

