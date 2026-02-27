"""
keyboards.py — все inline клавиатуры бота
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


# ── Проверка поста ─────────────────────────────────────────────────────────────

def review_keyboard() -> InlineKeyboardMarkup:
    """Кнопки при выдаче поста жюри."""
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
    """Кнопка отмены при запросе причины пропуска."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Отмена", callback_data="skip_cancel")]
    ])


def reject_reason_keyboard() -> InlineKeyboardMarkup:
    """Кнопки выбора причины отклонения."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📏 Малое кол-во слов", callback_data="reject_few_words")],
        [InlineKeyboardButton("🤖 Использовался ИИ",  callback_data="reject_ai_used")],
        [InlineKeyboardButton("📝 Иное нарушение",    callback_data="reject_other")],
        [InlineKeyboardButton("◀️ Отмена",            callback_data="reject_cancel")],
    ])


def reject_custom_cancel_keyboard() -> InlineKeyboardMarkup:
    """Кнопка отмены при вводе своей причины отклонения."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Отмена", callback_data="reject_cancel")]
    ])


# ── Админ панель ───────────────────────────────────────────────────────────────

def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Статистика",   callback_data="admin_stats")],
        [InlineKeyboardButton("📋 Очередь",      callback_data="admin_queue")],
        [InlineKeyboardButton("👥 Проверяющие",  callback_data="admin_reviewers")],
        [InlineKeyboardButton("✅ Верификация",   callback_data="admin_verify")],
        [InlineKeyboardButton("📄 Логи",         callback_data="admin_logs")],
        [InlineKeyboardButton("🔴 Выключить бота", callback_data="admin_shutdown")],
    ])


def verify_keyboard(tg_id: str, is_verified: bool) -> InlineKeyboardMarkup:
    """Кнопки верификации конкретного жюри."""
    action = "admin_unverify_" if is_verified else "admin_verify_"
    label  = "❌ Отозвать верификацию" if is_verified else "✅ Верифицировать"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"{action}{tg_id}")],
        [InlineKeyboardButton("◀️ Назад", callback_data="admin_verify")],
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