"""
user.py — хендлеры для жюри

Команды: /start, /register, /next, /cancel, /stats, /fullstats
"""

import re
import random
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler

from utils.database import get_db
from utils.ai_utils import check_post
from parser.queue_manager import get_queue_posts, remove_from_queue, remove_from_all_queues, get_free_posts, assign_post
from utils.logger import setup_logger
from bot.keyboards import (
    review_keyboard,
    skip_cancel_keyboard,
    reject_reason_keyboard,
    reject_custom_cancel_keyboard,
)

log = setup_logger()

# Состояния ConversationHandler
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
            "SELECT 1 FROM reviewers WHERE TGID = ?", (tg_id,)
        ).fetchone() is not None


def _is_verified(tg_id: str) -> bool:
    """Возвращает True только если жюри верифицирован (Verified = 1)."""
    with get_db() as db:
        row = db.execute(
            "SELECT Verified FROM reviewers WHERE TGID = ?", (tg_id,)
        ).fetchone()
        return row is not None and row["Verified"] == 1


def _is_admin(tg_id: str) -> bool:
    with get_db() as db:
        row = db.execute(
            "SELECT IsAdmin FROM reviewers WHERE TGID = ?", (tg_id,)
        ).fetchone()
        return row is not None and row["IsAdmin"] == 1


def _register(tg_id: str, name: str, url: str) -> None:
    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO reviewers (TGID, URL, Name, IsAdmin, Verified) VALUES (?, ?, ?, 0, 0)",
            (tg_id, url, name),
        )
        db.commit()


def _try_reserve_post(post_id: int, tg_id: str, db) -> dict | None:
    """Пытается атомарно зарезервировать пост за жюри. Возвращает данные или None."""
    row = db.execute(
        """
        SELECT p.ID, p.URL, a.Name, r.BotWords, r.SkipReason, r.ID as result_id
        FROM posts_info p
        JOIN authors a ON p.Author = a.ID
        JOIN results r ON r.Post = p.ID
        WHERE p.ID = ?
          AND p.HumanChecked  = 0
          AND p.Rejected       = 0
          AND p.PostOfReviewer = 0
          AND r.Reviewer       IS NULL
        """,
        (post_id,),
    ).fetchone()

    if not row:
        return None

    updated = db.execute(
        "UPDATE results SET Reviewer = ? WHERE ID = ? AND Reviewer IS NULL",
        (tg_id, row["result_id"]),
    ).rowcount

    if not updated:
        return None

    return {
        "post_id":   row["ID"],
        "url":       row["URL"],
        "author":    row["Name"],
        "bot_words": row["BotWords"],
        "prev_skip": row["SkipReason"],
        "result_id": row["result_id"],
    }


def _get_next_post(tg_id: str) -> dict | None:
    """
    Атомарно резервирует пост для жюри.
    Сначала смотрит личную очередь жюри.
    Если она пуста — берёт из свободных постов (просроченные у других жюри).
    """
    # Сначала личная очередь
    post_ids = get_queue_posts(tg_id)

    # Если пусто — смотрим свободные посты
    if not post_ids:
        post_ids = get_free_posts()
        if not post_ids:
            return None
        # Назначаем первый свободный пост этому жюри
        assign_post(post_ids[0])
        post_ids = [post_ids[0]]

    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")

        for post_id in post_ids:
            result = _try_reserve_post(post_id, tg_id, db)
            if result:
                db.execute("COMMIT")
                return result

        db.execute("ROLLBACK")
        return None


def _get_active_post(tg_id: str) -> dict | None:
    """Возвращает пост который уже зарезервирован за жюри но ещё не проверен."""
    with get_db() as db:
        row = db.execute(
            """
            SELECT p.ID, p.URL, a.Name, r.BotWords, r.SkipReason, r.ID as result_id
            FROM results r
            JOIN posts_info p ON r.Post = p.ID
            JOIN authors   a ON p.Author = a.ID
            WHERE r.Reviewer = ? AND r.HumanWords IS NULL AND p.Rejected = 0
            """,
            (tg_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "post_id":   row["ID"],
            "url":       row["URL"],
            "author":    row["Name"],
            "bot_words": row["BotWords"],
            "prev_skip": row["SkipReason"],
            "result_id": row["result_id"],
        }


def _release_post(post_id: int, tg_id: str) -> None:
    """Освобождает пост обратно в очередь."""
    with get_db() as db:
        db.execute(
            "UPDATE results SET Reviewer = NULL WHERE Post = ? AND Reviewer = ? AND HumanWords IS NULL",
            (post_id, tg_id),
        )
        db.commit()


def _skip_post(result_id: int, tg_id: str, reason: str) -> bool:
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        updated = db.execute(
            """
            UPDATE results SET Reviewer = NULL, SkipReason = ?
            WHERE ID = ? AND Reviewer = ? AND HumanWords IS NULL
            """,
            (reason, result_id, tg_id),
        ).rowcount
        if not updated:
            db.execute("ROLLBACK")
            return False
        db.execute("COMMIT")
        return True


def _reject_post(post_id: int, result_id: int, tg_id: str, reason: str) -> bool:
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")

        exists = db.execute(
            "SELECT 1 FROM results WHERE ID = ? AND Reviewer = ? AND HumanWords IS NULL",
            (result_id, tg_id),
        ).fetchone()

        if not exists:
            db.execute("ROLLBACK")
            return False

        db.execute(
            "UPDATE posts_info SET Rejected = 1, HumanChecked = 1 WHERE ID = ?",
            (post_id,),
        )
        db.execute(
            "UPDATE results SET RejectReason = ? WHERE ID = ?",
            (reason, result_id),
        )
        db.execute("COMMIT")
        remove_from_all_queues(post_id)
        return True


def _save_result(post_id: int, result_id: int, tg_id: str, words: int, errors: int) -> bool:
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        updated = db.execute(
            """
            UPDATE results SET HumanWords = ?, HumanErrors = ?, SkipReason = NULL
            WHERE ID = ? AND Reviewer = ? AND HumanWords IS NULL
            """,
            (words, errors, result_id, tg_id),
        ).rowcount
        if not updated:
            db.execute("ROLLBACK")
            return False
        db.execute(
            "UPDATE posts_info SET HumanChecked = 1 WHERE ID = ?", (post_id,)
        )
        db.execute("COMMIT")
        remove_from_all_queues(post_id)
        return True


def _get_my_stats(tg_id: str) -> dict | None:
    with get_db() as db:
        row = db.execute(
            """
            SELECT
                rv.Name,
                COUNT(CASE WHEN r.HumanWords IS NOT NULL THEN 1 END) as checked,
                COUNT(CASE WHEN p.Rejected = 1 AND r.Reviewer = rv.TGID THEN 1 END) as rejected,
                COALESCE(SUM(r.HumanWords), 0)  as total_words,
                COALESCE(SUM(r.HumanErrors), 0) as total_errors
            FROM reviewers rv
            LEFT JOIN results   r ON r.Reviewer = rv.TGID
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


def _get_full_stats() -> dict:
    from parser.queue_manager import get_total_queue_count
    with get_db() as db:
        checked = db.execute(
            "SELECT COUNT(*) FROM posts_info WHERE HumanChecked = 1 AND Rejected = 0"
        ).fetchone()[0]
        rejected = db.execute(
            "SELECT COUNT(*) FROM posts_info WHERE Rejected = 1"
        ).fetchone()[0]
    in_queue = get_total_queue_count()
    return {"in_queue": in_queue, "checked": checked, "rejected": rejected}


# ── Команды ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    await update.message.reply_text("👋 Привет!", reply_markup=ReplyKeyboardRemove())

    if _is_registered(tg_id):
        if _is_verified(tg_id):
            commands = (
                "Ты зарегистрирован и верифицирован ✅\n\n"
                "/next — получить пост\n"
                "/stats — моя статистика\n"
                "/fullstats — общая статистика"
            )
            if _is_admin(tg_id):
                commands += "\n/admin — панель администратора"
        else:
            commands = (
                "Ты зарегистрирован ✅\n"
                "⏳ Ожидаешь верификации от администратора.\n\n"
                "/stats — моя статистика"
            )
        await update.message.reply_text(commands)
    else:
        await update.message.reply_text(
            "Для регистрации используй команду /register"
        )


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
        "⏳ Дождись верификации от администратора — после этого сможешь получать посты через /next.\n\n"
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

    errors_per_1000 = (
        round(data["total_errors"] / data["total_words"] * 1000, 2)
        if data["total_words"] else 0
    )

    await update.message.reply_text(
        f"📊 Твоя статистика, {data['name']}:\n\n"
        f"✅ Проверено: {data['checked']}\n"
        f"❌ Отклонено: {data['rejected']}\n"
        f"📝 Всего слов: {data['total_words']}\n"
        f"📉 Ошибок на 1000 слов: {errors_per_1000}"
    )


async def cmd_fullstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)

    if not _is_registered(tg_id):
        await update.message.reply_text("Сначала зарегистрируйся через /register")
        return

    s = _get_full_stats()
    await update.message.reply_text(
        f"📊 Общая статистика:\n\n"
        f"📋 В очереди: {s['in_queue']}\n"
        f"✅ Проверено: {s['checked']}\n"
        f"❌ Отклонено: {s['rejected']}"
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
            "Дождись подтверждения от администратора — после этого сможешь проверять посты."
        )
        return ConversationHandler.END

    # Если у жюри уже есть активный пост — показываем его снова
    post = _get_active_post(tg_id)
    if post:
        log.info(f"[next] {name} уже имеет активный пост #{post['post_id']}")
        context.user_data["post"] = post
    else:
        post = _get_next_post(tg_id)
        if not post:
            await update.message.reply_text("Все посты проверены или нет доступных! ✅")
            return ConversationHandler.END
        log.info(f"[next] {name} взял пост #{post['post_id']} — {post['url']}")
        context.user_data["post"] = post

    skip_note = f"\n⚠️ Предыдущий жюри пропустил: {post['prev_skip']}\n" if post["prev_skip"] else ""

    await update.message.reply_text(
        f"📝 Пост для проверки:\n{post['url']}\n\n"
        f"👤 Автор: {post['author']}\n"
        f"📊 Бот насчитал: {post['bot_words']} слов"
        f"{skip_note}\n\n"
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
    name  = update.effective_user.username or update.effective_user.first_name
    post  = context.user_data.get("post")
    words = context.user_data.get("words")

    if not post:
        await update.message.reply_text("Ошибка сессии. Попробуй /next снова.")
        return ConversationHandler.END

    success = _save_result(post["post_id"], post["result_id"], tg_id, words, int(text))
    if success:
        log.info(f"[check] {name} → #{post['post_id']}: слов={words}, ошибок={text}")
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
        _release_post(post["post_id"], tg_id)
        log.info(f"[cancel] {name} отменил пост #{post['post_id']}")

    context.user_data.clear()
    await update.message.reply_text("Отменено. /next — начать заново.")
    return ConversationHandler.END


# ── Проверка ИИ ───────────────────────────────────────────────────────────────

async def cb_ai_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    post = context.user_data.get("post")
    if not post:
        await query.message.reply_text("Нет активного поста. Попробуй /next.")
        return WAITING_WORDS

    # Получаем текст поста из БД
    with get_db() as db:
        row = db.execute(
            "SELECT Text FROM posts_info WHERE ID = ?", (post["post_id"],)
        ).fetchone()

    if not row or not row["Text"]:
        await query.message.reply_text("⚠️ Текст поста не найден в БД.")
        return WAITING_WORDS

    await query.message.reply_text("🤖 Отправляю текст на проверку, подожди...")

    results = check_post(row["Text"])
    for msg in results:
        # Telegram лимит — 4096 символов. Нарезаем если длиннее
        if len(msg) <= 4096:
            await query.message.reply_text(msg)
        else:
            # Режем по 4000 символов с запасом
            for i in range(0, len(msg), 4000):
                await query.message.reply_text(msg[i:i+4000])

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
    name   = update.effective_user.username or update.effective_user.first_name
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

    success = _skip_post(post["result_id"], tg_id, reason)
    if success:
        log.info(f"[skip] {name} → #{post['post_id']}: {reason}")
        await update.message.reply_text(
            f"⏭️ Пропущено (причина: {reason}).\n"
            f"Пост вернулся в очередь.\n\n"
            f"/next — следующий пост"
        )
    else:
        await update.message.reply_text("⚠️ Не удалось пропустить. Попробуй /next.")

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
    query  = update.callback_query
    await query.answer()
    tg_id  = str(query.from_user.id)
    name   = query.from_user.username or query.from_user.first_name
    post   = context.user_data.get("post")

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

    success = _reject_post(post["post_id"], post["result_id"], tg_id, reason)
    if success:
        log.info(f"[reject] {name} → #{post['post_id']}: {reason}")
        await query.message.reply_text(
            f"❌ Пост отклонён.\n"
            f"Он удалён из очереди.\n\n"
            f"/next — следующий пост"
        )
    else:
        await query.message.reply_text("⚠️ Не удалось отклонить. Попробуй /next.")

    context.user_data.clear()
    return ConversationHandler.END


async def got_reject_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id  = str(update.effective_user.id)
    name   = update.effective_user.username or update.effective_user.first_name
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
    success = _reject_post(post["post_id"], post["result_id"], tg_id, full_reason)
    if success:
        log.info(f"[reject] {name} → #{post['post_id']}: {full_reason}")
        await update.message.reply_text(
            f"❌ Пост отклонён (причина: {reason}).\n\n"
            f"/next — следующий пост"
        )
    else:
        await update.message.reply_text("⚠️ Не удалось отклонить. Попробуй /next.")

    context.user_data.clear()
    return ConversationHandler.END


