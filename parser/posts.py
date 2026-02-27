"""
posts.py — парсинг постов через requests + BeautifulSoup

Логика:
1. Берём ссылки с Parsed = 0 из таблицы links
2. Для каждой ссылки загружаем страницу и извлекаем автора и текст
3. Считаем слова ботом
4. Если автор — верифицированный жюри, сохраняем с PostOfReviewer=1 без очереди
5. Иначе сохраняем пост в posts_info, results и распределяем в очередь через queue_manager
6. Помечаем ссылку как обработанную (Parsed = 1)
"""

import time
import requests
from bs4 import BeautifulSoup

from utils.database import get_db
from utils.logger import setup_logger
from utils.word_counter import count_words
from parser.queue_manager import assign_post, ensure_all_queues

log = setup_logger()

PAGE_PAUSE = 1.5  # секунды между запросами

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; Raspberry Pi) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}


# ── БД ────────────────────────────────────────────────────────────────────────

def _get_unparsed_links() -> list[str]:
    """Возвращает ссылки с Parsed = 0."""
    with get_db() as db:
        rows = db.execute("SELECT URL FROM links WHERE Parsed = 0").fetchall()
        return [row["URL"] for row in rows]


def _get_verified_reviewer_urls() -> set[str]:
    """Возвращает URL профилей жюри с Verified = 1."""
    with get_db() as db:
        rows = db.execute(
            "SELECT URL FROM reviewers WHERE Verified = 1"
        ).fetchall()
        return {row["URL"] for row in rows}


def _get_current_day() -> int | None:
    """Возвращает ID текущего (последнего) дня конкурса."""
    with get_db() as db:
        row = db.execute("SELECT MAX(Day) FROM days").fetchone()
        return row[0] if row and row[0] else None


def _mark_link_parsed(url: str) -> None:
    """Помечает ссылку как обработанную."""
    with get_db() as db:
        db.execute("UPDATE links SET Parsed = 1 WHERE URL = ?", (url,))
        db.commit()


def _save_post(
    url: str,
    author_name: str,
    author_url: str,
    text: str,
    day: int,
    bot_words: int,
    is_reviewer: bool,
) -> None:
    """
    Сохраняет пост в posts_info.
    Если автор — жюри, ставит PostOfReviewer = 1 и не создаёт записи в results и queue.
    """
    with get_db() as db:
        # Автор — добавляем если не существует
        row = db.execute("SELECT ID FROM authors WHERE URL = ?", (author_url,)).fetchone()
        if row:
            author_id = row["ID"]
        else:
            cursor = db.execute(
                "INSERT INTO authors (Name, URL) VALUES (?, ?)",
                (author_name, author_url),
            )
            author_id = cursor.lastrowid

        post_of_reviewer = 1 if is_reviewer else 0

        # Пост
        cursor = db.execute(
            """
            INSERT OR IGNORE INTO posts_info
                (Author, URL, Text, Day, HumanChecked, PostOfReviewer, Rejected)
            VALUES (?, ?, ?, ?, 0, ?, 0)
            """,
            (author_id, url, text, day, post_of_reviewer),
        )
        post_id = cursor.lastrowid

        # Если пост уже существовал — получаем его ID
        if not post_id:
            row = db.execute("SELECT ID FROM posts_info WHERE URL = ?", (url,)).fetchone()
            post_id = row["ID"] if row else None

        if post_id and not is_reviewer:
            db.execute(
                "INSERT OR IGNORE INTO results (Post, BotWords) VALUES (?, ?)",
                (post_id, bot_words),
            )

        db.commit()
        return post_id if not is_reviewer else None


# ── Парсинг страницы ──────────────────────────────────────────────────────────

def _parse_page(url: str) -> dict | None:
    """
    Загружает страницу поста и извлекает автора и текст.
    Возвращает None если не удалось получить данные.
    """
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = "utf-8"
    except requests.RequestException as e:
        log.error(f"[posts] Ошибка запроса {url}: {e}")
        return None

    try:
        soup = BeautifulSoup(response.text, "html.parser")

        # Автор — первая ссылка на /user/ с непустым текстом
        author_name = ""
        author_url  = ""
        for a in soup.select("a[href*='/user/']"):
            name = a.get_text(strip=True)
            if name:
                author_name = name
                href = a.get("href", "")
                author_url = f"https://inkstory.net{href}" if href.startswith("/") else href
                break

        if not author_name:
            log.warning(f"[posts] Не найден автор: {url}")
            return None

        # Текст поста — перебираем селекторы от точного к общему
        text = ""
        for selector in [
            "div.prose.prose-sm p.max-w-full",
            "div.prose p",
            "div.prose",
        ]:
            blocks = soup.select(selector)
            if blocks:
                text = "\n".join(
                    b.get_text(strip=True) for b in blocks if b.get_text(strip=True)
                )
                if text:
                    break

        if not text:
            log.warning(f"[posts] Не удалось найти текст: {url}")

        return {
            "author_name": author_name,
            "author_url":  author_url,
            "text":        text,
        }

    except Exception as e:
        log.error(f"[posts] Ошибка парсинга {url}: {e}")
        return None


# ── Основная функция ──────────────────────────────────────────────────────────

def parse() -> int:
    """
    Парсит все ссылки с Parsed = 0 и сохраняет посты в БД.
    Возвращает количество новых постов добавленных в очередь.
    """
    # Убеждаемся что у всех верифицированных жюри есть очереди
    ensure_all_queues()

    day = _get_current_day()
    if not day:
        log.error("[posts] Нет активного дня в таблице days")
        return 0

    links                  = _get_unparsed_links()
    verified_reviewer_urls = _get_verified_reviewer_urls()
    assigned: dict[str, int] = {}  # tgid → количество постов

    log.info(f"[posts] Ссылок для парсинга: {len(links)}, день: {day}")

    for i, url in enumerate(links, 1):
        log.debug(f"[posts] [{i}/{len(links)}] {url}")

        post = _parse_page(url)

        # Помечаем ссылку как обработанную даже при ошибке — чтобы не зациклиться
        _mark_link_parsed(url)

        if not post:
            continue

        is_reviewer = post["author_url"] in verified_reviewer_urls
        bot_words   = count_words(post["text"])

        if is_reviewer:
            log.info(f"[posts] Пост верифицированного жюри — сохраняем без очереди: {post['author_name']} — {url}")

        post_id = _save_post(
            url=url,
            author_name=post["author_name"],
            author_url=post["author_url"],
            text=post["text"],
            day=day,
            bot_words=bot_words,
            is_reviewer=is_reviewer,
        )

        log.info(f"[posts] Сохранён: {post['author_name']} — {url} ({bot_words} слов)")

        if post_id:
            tgid = assign_post(post_id)
            if tgid:
                assigned[tgid] = assigned.get(tgid, 0) + 1

        time.sleep(PAGE_PAUSE)

    total = sum(assigned.values())
    log.info(f"[posts] Готово. Новых постов в очереди: {total}")
    return assigned