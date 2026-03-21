"""
bot/handlers/common.py — общие команды: /start, /register, /stats, /fullstats
"""

import re
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler

from utils.logger import setup_logger
from utils.config import MAX_WORDS, MAX_ERRORS
from utils.db.jury import (
    is_registered, is_verified, is_admin,
    register_reviewer, get_my_stats,
)
from utils.db.posts import errors_per_1000, get_posts_stats
from parser.queue_manager import get_total_queue_count

log = setup_logger()

WAITING_REG_URL = 0


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    await update.message.reply_text("👋 Привет!", reply_markup=ReplyKeyboardRemove())

    if not is_registered(tg_id):
        await update.message.reply_text("Для регистрации используй /register")
        return

    if not is_verified(tg_id):
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
    if is_admin(tg_id):
        commands += "\n/admin — панель администратора"
    await update.message.reply_text(commands)


async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    if is_registered(tg_id):
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

    register_reviewer(tg_id, name, url)
    log.info(f"[register] {name} ({tg_id}) → {url}")
    await update.message.reply_text(
        "✅ Зарегистрирован!\n\n"
        "⏳ Дождись верификации от администратора.\n\n"
        "/stats — моя статистика"
    )
    return ConversationHandler.END


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    if not is_registered(tg_id):
        await update.message.reply_text("Сначала зарегистрируйся через /register")
        return

    data = get_my_stats(tg_id)
    if not data:
        await update.message.reply_text("Не удалось получить статистику.")
        return

    ep1k = errors_per_1000(data["total_errors"], data["total_words"])
    await update.message.reply_text(
        f"📊 Твоя статистика, {data['name']}:\n\n"
        f"✅ Проверено: {data['checked']}\n"
        f"❌ Отклонено: {data['rejected']}\n"
        f"📝 Всего слов: {data['total_words']}\n"
        f"📉 Ошибок на 1000 слов: {ep1k}"
    )


async def cmd_fullstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    if not is_registered(tg_id):
        await update.message.reply_text("Сначала зарегистрируйся через /register")
        return

    stats = get_posts_stats()
    await update.message.reply_text(
        f"📊 Общая статистика:\n\n"
        f"📋 В очереди: {get_total_queue_count()}\n"
        f"✅ Проверено: {stats['done']}\n"
        f"❌ Отклонено: {stats['rejected']}"
    )
