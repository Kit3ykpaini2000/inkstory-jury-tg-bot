"""
utils/ai_utils.py — проверка текста поста через GitHub Models (gpt-4o-mini)

GitHub Models использует OpenAI-совместимый API.
Токен: github.com/settings/tokens (права не нужны)
"""

from utils.config import GITHUB_TOKEN, GITHUB_MODEL, AI_CHUNK_SIZE
from utils.logger import setup_logger

log = setup_logger()

SYSTEM_PROMPT = """Ты — строгий корректор текста на русском языке.
Твоя задача — найти ТОЛЬКО реальные ошибки: орфографические, грамматические, пунктуационные.
НЕ предлагай стилистические правки и НЕ указывай на то, что ошибкой не является.

Формат ответа — СТРОГО следующий:
Найдено ошибок: <число>

1. "<неправильно>" → "<правильно>" — <краткое объяснение>
2. "<неправильно>" → "<правильно>" — <краткое объяснение>
...

Если ошибок нет — напиши ТОЛЬКО: Ошибок не найдено ✅
Не добавляй никакого другого текста до или после."""


def _get_client():
    if not GITHUB_TOKEN:
        raise ValueError("GITHUB_TOKEN не задан в .env")
    from openai import OpenAI
    return OpenAI(
        base_url="https://models.inference.ai.azure.com",
        api_key=GITHUB_TOKEN,
    )


def _split_text(text: str) -> list[str]:
    """Разбивает текст на части не превышая AI_CHUNK_SIZE символов."""
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
            model=GITHUB_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": f"Проверь текст:\n\n{text}"},
            ],
            temperature=0.1,
            max_tokens=2048,
        )
        return f"{prefix}{response.choices[0].message.content.strip()}"
    except Exception as e:
        log.error(f"[ai] Ошибка GitHub Models API: {e}")
        return f"{prefix}⚠️ Ошибка при проверке: {e}"


def check_post(text: str) -> list[str]:
    """
    Проверяет текст поста через GitHub Models.
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
