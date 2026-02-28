"""
bot/handlers/user.py — хендлеры для жюри

Команды: /start, /register, /next, /cancel, /stats, /fullstats
"""

import re
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler

from utils.database import get_db
from utils.ai_utils import check_post
from utils.logger import setup_logger
from parser.queue_manager import (
    take_post, get_active_post, release_post, remove_post,
    get_total_queue_count,
)
from bot.keyboards import (
    review_keyboard, skip_cancel_keyboard,
    reject_reason_keyboard, reject_custom_cancel_keyboard,
)

log = setup_logger()

(
    WAITING_REG_URL,
    WAITING_WORDS,
    WAITING_ERRORS,
    WAITING_SKIP_TEXT,
    WAITING_REJECT_REASON,
    WAITING_REJECT_CUSTOM,
) = range(6)

MAX_WORDS  = 100_000
MAX_ERRORS = 10_000


# ── Хелперы БД ────────────────────────────────────────────────────────────────

def _is_registered(tg_id: str) -> bool:
    with get_db() as db:
        return db.execute(
            "SELECT 1 FROM reviewers WHERE TGID=?", (tg_id,)
        ).fetchone() is not None


def _is_verified(tg_id: str) -> bool:
    with get_db() as db:
        row = db.execute(
            "SELECT Verified FROM reviewers WHERE TGID=?", (tg_id,)
        ).fetchone()
        return row is not None and row["Verified"] == 1


def _is_admin(tg_id: str) -> bool:
    with get_db() as db:
        row = db.execute(
            "SELECT IsAdmin FROM reviewers WHERE TGID=?", (tg_id,)
        ).fetchone()
        return row is not None and row["IsAdmin"] == 1


def _register(tg_id: str, name: str, url: str) -> None:
    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO reviewers (TGID, URL, Name, IsAdmin, Verified) VALUES (?,?,?,0,0)",
            (tg_id, url, name),
        )
        db.commit()


def _skip_post(post_id: int, tgid: str, reason: str) -> None:
    """Освобождает пост с причиной пропуска (пишем в лог, пост возвращается в очередь)."""
    release_post(tgid, post_id)
    log.info(f"[skip] {tgid} → #{post_id}: {reason}")


def _reject_post(post_id: int, tgid: str, reason: str) -> bool:
    """Отклоняет пост — меняет статус и сохраняет причину."""
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")

        # Проверяем что пост действительно у этого жюри
        row = db.execute(
            "SELECT Post FROM queue WHERE Post=? AND Reviewer=? AND TakenAt IS NOT NULL",
            (post_id, tgid),
        ).fetchone()

        if not row:
            db.execute("ROLLBACK")
            return False

        db.execute(
            "UPDATE posts_info SET Status='rejected' WHERE ID=?",
            (post_id,),
        )
        db.execute(
            "UPDATE results SET RejectReason=?, Reviewer=? WHERE Post=?",
            (reason, tgid, post_id),
        )
        db.execute("COMMIT")

    remove_post(post_id)
    log.info(f"[reject] {tgid} → #{post_id}: {reason}")
    return True


def _save_result(post_id: int, tgid: str, words: int, errors: int) -> bool:
    """Сохраняет результат проверки."""
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")

        row = db.execute(
            "SELECT Post FROM queue WHERE Post=? AND Reviewer=? AND TakenAt IS NOT NULL",
            (post_id, tgid),
        ).fetchone()

        if not row:
            db.execute("ROLLBACK")
            return False

        db.execute(
            "UPDATE results SET HumanWords=?, HumanErrors=?, Reviewer=? WHERE Post=?",
            (words, errors, tgid, post_id),
        )
        db.execute(
            "UPDATE posts_info SET Status='done' WHERE ID=?",
            (post_id,),
        )
        db.execute("COMMIT")

    remove_post(post_id)
    log.info(f"[check] {tgid} → #{post_id}: слов={words}, ошибок={errors}")
    return True


def _get_my_stats(tg_id: str) -> dict | None:
    with get_db() as db:
        row = db.execute(
            """
            SELECT
                rv.Name,
                COUNT(CASE WHEN r.HumanWords IS NOT NULL THEN 1 END) AS checked,
                COUNT(CASE WHEN p.Status='rejected' AND r.Reviewer=rv.TGID THEN 1 END) AS rejected,
                COALESCE(SUM(r.HumanWords),  0) AS total_words,
                COALESCE(SUM(r.HumanErrors), 0) AS total_errors
            FROM reviewers rv
            LEFT JOIN results    r ON r.Reviewer = rv.TGID
            LEFT JOIN posts_info p ON p.ID = r.Post
            WHERE rv.TGID = ?
            GROUP BY rv.TGID
            """,
            (tg_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "name":         row["Name"],
        "checked":      row["checked"],
        "rejected":     row["rejected"],
        "total_words":  row["total_words"],
        "total_errors": row["total_errors"],
    }


# ── Команды ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    await update.message.reply_text("👋 Привет!", reply_markup=ReplyKeyboardRemove())

    if not _is_registered(tg_id):
        await update.message.reply_text("Для регистрации используй /register")
        return

    if not _is_verified(tg_id):
        await update.message.reply_text(
            "Ты зарегистрирован ✅\n"
            "⏳ Ожидаешь верификации от администратора.\n\n"
            "/stats — моя статистика"
        )
        return

    commands = (
        "Ты зарегистрирован и верифицирован ✅\n\n"
        "/next — получить пост\n"
        "/stats — моя статистика\n"
        "/fullstats — общая статистика"
    )
    if _is_admin(tg_id):
        commands += "\n/admin — панель администратора"
    await update.message.reply_text(commands)


async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    if _is_registered(tg_id):
        await update.message.reply_text("Ты уже зарегистрирован ✅")
        return ConversationHandler.END

    await update.message.reply_text(
        "Отправь ссылку на свой профиль на inkstory.net\n"
        "Формат: https://inkstory.net/user/username"
    )
    return WAITING_REG_URL


async def got_reg_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    name  = update.effective_user.username or update.effective_user.first_name
    url   = update.message.text.strip().rstrip("/")

    if not re.match(r"https://inkstory\.net/user/[\w\-]+$", url):
        await update.message.reply_text(
            "⚠️ Неверная ссылка. Нужен формат: https://inkstory.net/user/username"
        )
        return WAITING_REG_URL

    _register(tg_id, name, url)
    log.info(f"[register] {name} ({tg_id}) → {url}")
    await update.message.reply_text(
        "✅ Зарегистрирован!\n\n"
        "⏳ Дождись верификации от администратора.\n\n"
        "/stats — моя статистика"
    )
    return ConversationHandler.END


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    if not _is_registered(tg_id):
        await update.message.reply_text("Сначала зарегистрируйся через /register")
        return

    data = _get_my_stats(tg_id)
    if not data:
        await update.message.reply_text("Не удалось получить статистику.")
        return

    ep1k = (
        round(data["total_errors"] / data["total_words"] * 1000, 2)
        if data["total_words"] else 0
    )
    await update.message.reply_text(
        f"📊 Твоя статистика, {data['name']}:\n\n"
        f"✅ Проверено: {data['checked']}\n"
        f"❌ Отклонено: {data['rejected']}\n"
        f"📝 Всего слов: {data['total_words']}\n"
        f"📉 Ошибок на 1000 слов: {ep1k}"
    )


async def cmd_fullstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    if not _is_registered(tg_id):
        await update.message.reply_text("Сначала зарегистрируйся через /register")
        return

    with get_db() as db:
        checked  = db.execute(
            "SELECT COUNT(*) FROM posts_info WHERE Status='done'"
        ).fetchone()[0]
        rejected = db.execute(
            "SELECT COUNT(*) FROM posts_info WHERE Status='rejected'"
        ).fetchone()[0]

    await update.message.reply_text(
        f"📊 Общая статистика:\n\n"
        f"📋 В очереди: {get_total_queue_count()}\n"
        f"✅ Проверено: {checked}\n"
        f"❌ Отклонено: {rejected}"
    )


async def cmd_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    name  = update.effective_user.username or update.effective_user.first_name

    if not _is_registered(tg_id):
        await update.message.reply_text("Сначала зарегистрируйся через /register")
        return ConversationHandler.END

    if not _is_verified(tg_id):
        await update.message.reply_text(
            "⏳ Твоя учётная запись ещё не верифицирована.\n"
            "Дождись подтверждения от администратора."
        )
        return ConversationHandler.END

    # Сначала смотрим есть ли уже активный пост
    post = get_active_post(tg_id)
    if post:
        log.info(f"[next] {name} уже имеет активный пост #{post['post_id']}")
    else:
        post = take_post(tg_id)
        if not post:
            await update.message.reply_text("Все посты проверены или нет доступных! ✅")
            return ConversationHandler.END
        log.info(f"[next] {name} взял пост #{post['post_id']}")

    context.user_data["post"] = post

    await update.message.reply_text(
        f"📝 Пост для проверки:\n{post['url']}\n\n"
        f"👤 Автор: {post['author']}\n"
        f"📊 Бот насчитал: {post['bot_words']} слов\n\n"
        f"Введите количество слов:",
        reply_markup=review_keyboard(),
    )
    return WAITING_WORDS


async def got_words(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) > MAX_WORDS:
        await update.message.reply_text(f"Введите число от 0 до {MAX_WORDS}:")
        return WAITING_WORDS

    context.user_data["words"] = int(text)
    await update.message.reply_text("Введите количество ошибок:")
    return WAITING_ERRORS


async def got_errors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) > MAX_ERRORS:
        await update.message.reply_text(f"Введите число от 0 до {MAX_ERRORS}:")
        return WAITING_ERRORS

    tg_id = str(update.effective_user.id)
    post  = context.user_data.get("post")
    words = context.user_data.get("words")

    if not post:
        await update.message.reply_text("Ошибка сессии. Попробуй /next снова.")
        return ConversationHandler.END

    success = _save_result(post["post_id"], tg_id, words, int(text))
    if success:
        await update.message.reply_text("✅ Сохранено! /next — следующий пост")
    else:
        await update.message.reply_text("⚠️ Не удалось сохранить. Попробуй /next.")

    context.user_data.clear()
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    name  = update.effective_user.username or update.effective_user.first_name
    post  = context.user_data.get("post")

    if post:
        release_post(tg_id, post["post_id"])
        log.info(f"[cancel] {name} отменил пост #{post['post_id']}")

    context.user_data.clear()
    await update.message.reply_text("Отменено. /next — начать заново.")
    return ConversationHandler.END


# ── AI проверка ───────────────────────────────────────────────────────────────

async def cb_ai_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    post = context.user_data.get("post")
    if not post:
        await query.message.reply_text("Нет активного поста. Попробуй /next.")
        return WAITING_WORDS

    with get_db() as db:
        row = db.execute(
            "SELECT Text FROM posts_info WHERE ID=?", (post["post_id"],)
        ).fetchone()

    if not row or not row["Text"]:
        await query.message.reply_text("⚠️ Текст поста не найден.")
        return WAITING_WORDS

    await query.message.reply_text("🤖 Отправляю текст на проверку, подожди...")

    for msg in check_post(row["Text"]):
        for chunk in [msg[i:i+4000] for i in range(0, len(msg), 4000)]:
            await query.message.reply_text(chunk)

    return WAITING_WORDS


# ── Пропуск ───────────────────────────────────────────────────────────────────

async def cb_skip_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not context.user_data.get("post"):
        await query.message.reply_text("Нет активного поста. Попробуй /next.")
        return WAITING_WORDS

    await query.message.reply_text(
        "✏️ Укажи причину пропуска:",
        reply_markup=skip_cancel_keyboard(),
    )
    return WAITING_SKIP_TEXT


async def cb_skip_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("Хорошо, введи количество слов:")
    return WAITING_WORDS


async def got_skip_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id  = str(update.effective_user.id)
    reason = update.message.text.strip()
    post   = context.user_data.get("post")

    if not post:
        await update.message.reply_text("Нет активного поста. Попробуй /next.")
        return ConversationHandler.END

    if not reason:
        await update.message.reply_text(
            "Причина не может быть пустой:",
            reply_markup=skip_cancel_keyboard(),
        )
        return WAITING_SKIP_TEXT

    _skip_post(post["post_id"], tg_id, reason)
    await update.message.reply_text(
        f"⏭️ Пропущено (причина: {reason}).\n"
        f"Пост вернулся в очередь.\n\n/next — следующий пост"
    )
    context.user_data.clear()
    return ConversationHandler.END


# ── Отклонение ────────────────────────────────────────────────────────────────

async def cb_reject_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not context.user_data.get("post"):
        await query.message.reply_text("Нет активного поста. Попробуй /next.")
        return WAITING_WORDS

    await query.message.reply_text(
        "Укажи причину отклонения:",
        reply_markup=reject_reason_keyboard(),
    )
    return WAITING_REJECT_REASON


async def cb_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    post  = context.user_data.get("post")

    if query.data == "reject_cancel":
        await query.message.reply_text("Хорошо, введи количество слов:")
        return WAITING_WORDS

    if not post:
        await query.message.reply_text("Нет активного поста. Попробуй /next.")
        return ConversationHandler.END

    if query.data == "reject_other":
        await query.message.reply_text(
            "✏️ Опиши причину отклонения:",
            reply_markup=reject_custom_cancel_keyboard(),
        )
        return WAITING_REJECT_CUSTOM

    reason_map = {
        "reject_few_words": "few_words",
        "reject_ai_used":   "ai_used",
    }
    reason = reason_map.get(query.data, query.data)

    if _reject_post(post["post_id"], tg_id, reason):
        await query.message.reply_text("❌ Пост отклонён.\n\n/next — следующий пост")
    else:
        await query.message.reply_text("⚠️ Не удалось отклонить. Попробуй /next.")

    context.user_data.clear()
    return ConversationHandler.END


async def got_reject_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id  = str(update.effective_user.id)
    reason = update.message.text.strip()
    post   = context.user_data.get("post")

    if not post:
        await update.message.reply_text("Нет активного поста. Попробуй /next.")
        return ConversationHandler.END

    if not reason:
        await update.message.reply_text(
            "Причина не может быть пустой:",
            reply_markup=reject_custom_cancel_keyboard(),
        )
        return WAITING_REJECT_CUSTOM

    full_reason = f"other: {reason}"
    if _reject_post(post["post_id"], tg_id, full_reason):
        await update.message.reply_text(
            f"❌ Пост отклонён (причина: {reason}).\n\n/next — следующий пост"
        )
    else:
        await update.message.reply_text("⚠️ Не удалось отклонить. Попробуй /next.")

    context.user_data.clear()
    return ConversationHandler.END
