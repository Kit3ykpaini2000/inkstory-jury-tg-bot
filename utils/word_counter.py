"""
word_counter.py — подсчёт слов в тексте

Правила:
- Считаются токены содержащие хотя бы одну букву (русскую или латинскую)
- НЕ считаются: числа, знаки препинания, тире/дефисы отдельно, эмодзи
- Слово с дефисом внутри (само-достаточный) = одно слово
"""

import re
import unicodedata


# Разбивка текста на токены по пробелам и переносам строк
_SPLIT_RE = re.compile(r"\s+")

# Числа: целые, дробные, с % или °
_NUMBER_RE = re.compile(r"^\d[\d.,]*\d*[%°]?$")

# Одиночные знаки препинания и тире/дефисы
_PUNCT_RE = re.compile(
    r"^[.,!?:;()\[\]{}<>\"'«»„""''–—…·•*#+=/\\|@^~`_\-]+$"
)


def _is_emoji(char: str) -> bool:
    cp = ord(char)
    return (
        0x1F600 <= cp <= 0x1F64F
        or 0x1F300 <= cp <= 0x1F5FF
        or 0x1F680 <= cp <= 0x1F6FF
        or 0x1F700 <= cp <= 0x1F77F
        or 0x1F780 <= cp <= 0x1F7FF
        or 0x1F800 <= cp <= 0x1F8FF
        or 0x1F900 <= cp <= 0x1F9FF
        or 0x1FA00 <= cp <= 0x1FA6F
        or 0x1FA70 <= cp <= 0x1FAFF
        or 0x2600 <= cp <= 0x26FF
        or 0x2700 <= cp <= 0x27BF
        or 0xFE00 <= cp <= 0xFE0F
        or 0x1F1E0 <= cp <= 0x1F1FF
    )


def _has_letter(token: str) -> bool:
    """Возвращает True если токен содержит хотя бы одну букву."""
    return any(unicodedata.category(ch).startswith("L") for ch in token)


def count_words(text: str) -> int:
    """
    Считает количество слов в тексте.
    Возвращает 0 если текст пустой.
    """
    if not text or not text.strip():
        return 0

    count = 0

    for raw_token in _SPLIT_RE.split(text.strip()):
        if not raw_token:
            continue

        # Убираем окружающую пунктуацию
        token = raw_token.strip(".,!?:;()[]{}\"'«»„""''…·•")

        if not token:
            continue

        # Пропускаем эмодзи
        if all(_is_emoji(ch) or unicodedata.category(ch) in ("So", "Mn", "Cf") for ch in token):
            continue

        # Пропускаем числа
        if _NUMBER_RE.match(token):
            continue

        # Пропускаем знаки препинания и тире
        if _PUNCT_RE.match(token):
            continue

        # Считаем только если есть буква
        if _has_letter(token):
            count += 1

    return count