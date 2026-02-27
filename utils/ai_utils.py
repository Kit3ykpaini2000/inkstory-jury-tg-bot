"""
ai_utils.py — проверка текста поста через Groq API

Логика:
1. Текст разбивается на части если он слишком большой
2. Каждая часть отправляется в Groq API
3. ИИ проверяет орфографию, грамматику, пунктуацию
4. Возвращает список строк для отправки жюри
5. Результат в БД не сохраняется
"""

import os
from groq import Groq

from utils.logger import setup_logger

log = setup_logger()

# Максимум символов на одну часть (с запасом для промпта)
MAX_CHUNK_SIZE = 3000

SYSTEM_PROMPT = """Ты — строгий корректор текста на русском языке.
Твоя задача — найти ВСЕ ошибки в тексте: орфографические, грамматические, пунктуационные, стилистические.

Формат ответа — СТРОГО следующий:
Найдено ошибок: <число>

1. "<неправильно>" → "<правильно>" — <краткое объяснение>
2. "<неправильно>" → "<правильно>" — <краткое объяснение>
...

Если ошибок нет — напиши: "Ошибок не найдено ✅"
Не добавляй никакого другого текста до или после."""

MODEL = "llama-3.3-70b-versatile"


def _get_client() -> Groq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY не найден в .env")
    return Groq(api_key=api_key)


def _split_text(text: str, chunk_size: int = MAX_CHUNK_SIZE) -> list[str]:
    """Разбивает текст на части по абзацам, не превышая chunk_size символов."""
    if len(text) <= chunk_size:
        return [text]

    paragraphs = text.split("\n")
    chunks     = []
    current    = ""

    for para in paragraphs:
        if len(current) + len(para) + 1 > chunk_size:
            if current:
                chunks.append(current.strip())
            current = para
        else:
            current += "\n" + para if current else para

    if current.strip():
        chunks.append(current.strip())

    return chunks or [text]


def _check_chunk(client: Groq, text: str, part: int, total: int) -> str:
    """Отправляет одну часть текста на проверку. Возвращает ответ ИИ."""
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
        result = response.choices[0].message.content.strip()
        return f"{prefix}{result}"

    except Exception as e:
        log.error(f"[ai] Ошибка Groq API: {e}")
        return f"{prefix}⚠️ Ошибка при проверке: {e}"


def check_post(text: str) -> list[str]:
    """
    Проверяет текст поста через Groq.
    Возвращает список сообщений для отправки жюри (одно или несколько если текст большой).
    """
    if not text or not text.strip():
        return ["⚠️ Текст поста пустой — нечего проверять."]

    try:
        client = _get_client()
    except ValueError as e:
        log.error(f"[ai] {e}")
        return [f"⚠️ {e}"]

    chunks  = _split_text(text.strip())
    total   = len(chunks)
    results = []

    log.info(f"[ai] Проверка поста: {total} часть(ей), {len(text)} символов")

    for i, chunk in enumerate(chunks, 1):
        result = _check_chunk(client, chunk, i, total)
        results.append(result)

    return results
