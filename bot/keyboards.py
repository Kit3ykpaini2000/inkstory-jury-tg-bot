"""
bot/keyboards.py — все inline клавиатуры бота
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏭️ Пропустить", callback_data="skip_post"),
            InlineKeyboardButton("❌ Отклонить",  callback_data="reject_post"),
        ],
        [
            InlineKeyboardButton("🤖 Проверка ИИ", callback_data="ai_check"),
        ],
    ])


def skip_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Отмена", callback_data="skip_cancel")]
    ])


def reject_reason_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📏 Мало слов",       callback_data="reject_few_words")],
        [InlineKeyboardButton("🤖 Использовался ИИ", callback_data="reject_ai_used")],
        [InlineKeyboardButton("📝 Иное нарушение",   callback_data="reject_other")],
        [InlineKeyboardButton("◀️ Отмена",           callback_data="reject_cancel")],
    ])


def reject_custom_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Отмена", callback_data="reject_cancel")]
    ])


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Статистика",      callback_data="admin_stats")],
        [InlineKeyboardButton("📋 Очередь",         callback_data="admin_queue")],
        [InlineKeyboardButton("👥 Проверяющие",     callback_data="admin_reviewers")],
        [InlineKeyboardButton("✅ Верификация",      callback_data="admin_verify")],
        [InlineKeyboardButton("⚙️ Режим очереди",   callback_data="admin_queue_mode")],
        [InlineKeyboardButton("📄 Логи",            callback_data="admin_logs")],
        [InlineKeyboardButton("🔴 Выключить бота",  callback_data="admin_shutdown")],
    ])


def queue_mode_keyboard(current_mode: str) -> InlineKeyboardMarkup:
    m = {x: "✅ " if current_mode == x else "" for x in ("open", "balanced")}
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{m['open']}🌐 Общая очередь",             callback_data="qmode_open")],
        [InlineKeyboardButton(f"{m['balanced']}⚖️ По суммарной нагрузке", callback_data="qmode_balanced")],
        [InlineKeyboardButton("◀️ Назад", callback_data="admin_back")],
    ])


def logs_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("50",  callback_data="logs_50"),
            InlineKeyboardButton("100", callback_data="logs_100"),
            InlineKeyboardButton("200", callback_data="logs_200"),
        ],
        [InlineKeyboardButton("◀️ Назад", callback_data="admin_back")],
    ])


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]
    ])


def verify_list_keyboard(reviewers: list[dict]) -> InlineKeyboardMarkup:
    """Список жюри для верификации/отзыва."""
    buttons = []
    for r in reviewers:
        status = "✅" if r["verified"] else "⏳"
        admin  = " 👑" if r["is_admin"] else ""
        label  = f"{status} {r['name']}{admin}"
        action = "admin_unverify_" if r["verified"] else "admin_verify_"
        buttons.append([InlineKeyboardButton(label, callback_data=f"{action}{r['tgid']}")])
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_back")])
    return InlineKeyboardMarkup(buttons)
