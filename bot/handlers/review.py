"""
bot/handlers/review.py — флоу проверки поста: /next, /cancel, ввод слов/ошибок,
                          пропуск, отклонение, AI-проверка
"""

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from utils.logger import setup_logger
from utils.config import MAX_WORDS, MAX_ERRORS
from utils.db.jury import is_registered, is_verified
from utils.db.posts import save_result, reject_post
from parser.queue_manager import take_post, get_active_post, release_post, remove_post
from parser.posts import fetch_post_text
from utils.ai_utils import check_post
from bot.keyboards import (
    review_keyboard, skip_cancel_keyboard,
    reject_reason_keyboard, reject_custom_cancel_keyboard,
)

log = setup_logger()

(
    WAITING_WORDS,
    WAITING_ERRORS,
    WAITING_SKIP_TEXT,
    WAITING_REJECT_REASON,
    WAITING_REJECT_CUSTOM,
) = range(1, 6)


async def cmd_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    name  = update.effective_user.username or update.effective_user.first_name

    if not is_registered(tg_id):
        await update.message.reply_text("Сначала зарегистрируйся через /register")
        return ConversationHandler.END

    if not is_verified(tg_id):
        await update.message.reply_text(
            "⏳ Твоя учётная запись ещё не верифицирована.\n"
            "Дождись подтверждения от администратора."
        )
        return ConversationHandler.END

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

    success = save_result(post["post_id"], tg_id, words, int(text))
    if success:
        remove_post(post["post_id"])
        log.info(f"[check] {tg_id} → #{post['post_id']}: слов={words}, ошибок={text}")
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

    await query.message.reply_text("🤖 Загружаю текст поста, подожди...")

    text = fetch_post_text(post["url"])
    if not text:
        await query.message.reply_text("⚠️ Не удалось загрузить текст поста.")
        return WAITING_WORDS

    await query.message.reply_text("🤖 Отправляю текст на проверку...")

    for msg in check_post(text):
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

    release_post(tg_id, post["post_id"])
    log.info(f"[skip] {tg_id} → #{post['post_id']}: {reason}")
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

    if reject_post(post["post_id"], tg_id, reason):
        remove_post(post["post_id"])
        log.info(f"[reject] {tg_id} → #{post['post_id']}: {reason}")
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
    if reject_post(post["post_id"], tg_id, full_reason):
        remove_post(post["post_id"])
        log.info(f"[reject] {tg_id} → #{post['post_id']}: {full_reason}")
        await update.message.reply_text(
            f"❌ Пост отклонён (причина: {reason}).\n\n/next — следующий пост"
        )
    else:
        await update.message.reply_text("⚠️ Не удалось отклонить. Попробуй /next.")

    context.user_data.clear()
    return ConversationHandler.END
