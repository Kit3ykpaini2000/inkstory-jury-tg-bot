"""
utils/ai_utils.py — проверка текста поста через Groq API
"""

from utils.config import GROQ_API_KEY, GROQ_MODEL, AI_CHUNK_SIZE
from utils.logger import setup_logger

log = setup_logger()

SYSTEM_PROMPT = """Ты — строгий корректор текста на русском языке.
Твоя задача — найти ВСЕ ошибки: орфографические, грамматические, пунктуационные, стилистические.

Формат ответа — СТРОГО следующий:
Найдено ошибок: <число>

1. "<неправильно>" → "<правильно>" — <краткое объяснение>
2. "<неправильно>" → "<правильно>" — <краткое объяснение>
...

Если ошибок нет — напиши: "Ошибок не найдено ✅"
Не добавляй никакого другого текста до или после."""


def _get_client():
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY не задан в .env")
    from groq import Groq
    return Groq(api_key=GROQ_API_KEY)


def _split_text(text: str) -> list[str]:
    """Разбивает текст на части по абзацам не превышая AI_CHUNK_SIZE символов."""
    if len(text) <= AI_CHUNK_SIZE:
        return [text]

    chunks, current = [], ""
    for para in text.split("\n"):
        if len(current) + len(para) + 1 > AI_CHUNK_SIZE:
            if current:
                chunks.append(current.strip())
            current = para
        else:
            current += ("\n" + para) if current else para

    if current.strip():
        chunks.append(current.strip())

    return chunks or [text]


def _check_chunk(client, text: str, part: int, total: int) -> str:
    prefix = f"[Часть {part}/{total}]\n\n" if total > 1 else ""
    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
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
