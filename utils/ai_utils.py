"""
utils/ai_utils.py — проверка текста поста через Groq API

Логика:
1. Текст разбивается на части если слишком большой (>3000 символов)
2. Каждая часть проверяется через Groq (llama-3.3-70b-versatile)
3. Возвращает список строк для отправки жюри
4. Результат в БД не сохраняется
"""

import os
from groq import Groq
from utils.logger import setup_logger

log = setup_logger()

MAX_CHUNK_SIZE = 3000
MODEL          = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """Ты — строгий корректор текста на русском языке.
Твоя задача — найти ВСЕ ошибки: орфографические, грамматические, пунктуационные, стилистические.

Формат ответа — СТРОГО следующий:
Найдено ошибок: <число>

1. "<неправильно>" → "<правильно>" — <краткое объяснение>
2. "<неправильно>" → "<правильно>" — <краткое объяснение>
...

Если ошибок нет — напиши: "Ошибок не найдено ✅"
Не добавляй никакого другого текста до или после."""


def _get_client() -> Groq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY не найден в .env")
    return Groq(api_key=api_key)


def _split_text(text: str, chunk_size: int = MAX_CHUNK_SIZE) -> list[str]:
    """Разбивает текст на части по абзацам не превышая chunk_size символов."""
    if len(text) <= chunk_size:
        return [text]

    chunks, current = [], ""
    for para in text.split("\n"):
        if len(current) + len(para) + 1 > chunk_size:
            if current:
                chunks.append(current.strip())
            current = para
        else:
            current += ("\n" + para) if current else para

    if current.strip():
        chunks.append(current.strip())

    return chunks or [text]


def _check_chunk(client: Groq, text: str, part: int, total: int) -> str:
    prefix = f"[Часть {part}/{total}]\n\n" if total > 1 else ""
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": f"Проверь текст:\n\n{text}"},
            ],
            temperature=0.1,
            max_tokens=2048,
        )
        return f"{prefix}{response.choices[0].message.content.strip()}"
    except Exception as e:
        log.error(f"[ai] Ошибка Groq API: {e}")
        return f"{prefix}⚠️ Ошибка при проверке: {e}"


def check_post(text: str) -> list[str]:
    """
    Проверяет текст поста через Groq.
    Возвращает список сообщений для отправки жюри.
    """
    if not text or not text.strip():
        return ["⚠️ Текст поста пустой — нечего проверять."]

    try:
        client = _get_client()
    except ValueError as e:
        log.error(f"[ai] {e}")
        return [f"⚠️ {e}"]

    chunks = _split_text(text.strip())
    log.info(f"[ai] Проверка: {len(chunks)} часть(ей), {len(text)} символов")

    return [_check_chunk(client, chunk, i, len(chunks)) for i, chunk in enumerate(chunks, 1)]
