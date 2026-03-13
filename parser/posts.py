"""
parser/posts.py — парсинг постов через requests + BeautifulSoup

Логика:
1. Берём ссылки с Parsed=0 из таблицы links
2. Загружаем страницу, извлекаем автора и текст
3. Если автор — верифицированный жюри → Status=reviewer_post, в очередь не идёт
4. Иначе → Status=pending, сохраняем в БД
5. Помечаем ссылку Parsed=1 только после успешного сохранения

Назначение постов в очередь (assign_post) выполняется в scheduler.py,
не здесь — parse() возвращает список новых post_id.
"""

import time
import requests
from bs4 import BeautifulSoup

from utils.database import get_db
from utils.config import PAGE_PAUSE_POSTS
from utils.logger import setup_logger
from utils.word_counter import count_words
from utils.constants import PostStatus

log = setup_logger()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; Raspberry Pi) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}


def _get_unparsed_links() -> list[str]:
    with get_db() as db:
        return [r["URL"] for r in db.execute(
            "SELECT URL FROM links WHERE Parsed=0"
        ).fetchall()]


def _get_verified_reviewer_urls() -> set[str]:
    with get_db() as db:
        return {r["URL"] for r in db.execute(
            "SELECT URL FROM reviewers WHERE Verified=1"
        ).fetchall()}


def _get_current_day() -> int | None:
    with get_db() as db:
        row = db.execute("SELECT MAX(Day) FROM days").fetchone()
        return row[0] if row and row[0] else None


def _mark_links_parsed(urls: list[str]) -> None:
    if not urls:
        return
    with get_db() as db:
        db.executemany(
            "UPDATE links SET Parsed=1 WHERE URL=?",
            [(url,) for url in urls],
        )
        db.commit()


def _save_post(
    url: str,
    author_name: str,
    author_url: str,
    text: str,
    day: int,
    bot_words: int,
    is_reviewer: bool,
) -> int | None:
    """
    Сохраняет пост атомарно.
    Возвращает post_id для постов участников или None для постов жюри/ошибки.
    """
    status = PostStatus.REVIEWER_POST if is_reviewer else PostStatus.PENDING

    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute(
                "SELECT ID FROM authors WHERE URL=?", (author_url,)
            ).fetchone()
            if row:
                author_id = row["ID"]
            else:
                cur = db.execute(
                    "INSERT INTO authors (Name, URL) VALUES (?,?)",
                    (author_name, author_url),
                )
                author_id = cur.lastrowid

            cur = db.execute(
                """
                INSERT OR IGNORE INTO posts_info (Author, URL, Text, Day, Status)
                VALUES (?,?,?,?,?)
                """,
                (author_id, url, text, day, status),
            )
            post_id = cur.lastrowid

            if not post_id:
                row = db.execute(
                    "SELECT ID FROM posts_info WHERE URL=?", (url,)
                ).fetchone()
                post_id = row["ID"] if row else None

            if post_id and not is_reviewer:
                db.execute(
                    "INSERT OR IGNORE INTO results (Post, BotWords) VALUES (?,?)",
                    (post_id, bot_words),
                )

            db.execute("COMMIT")
            return post_id if not is_reviewer else None

        except Exception as e:
            db.execute("ROLLBACK")
            log.error(f"[posts] Ошибка сохранения {url}: {e}")
            return None


def _parse_page(url: str) -> dict | None:
    """
    Загружает страницу и извлекает автора и текст.

    Возвращает:
      dict   — успешно
      None   — постоянная ошибка (404, нет автора) → ставить Parsed=1
      raises — временная ошибка (таймаут, 5xx)    → НЕ ставить Parsed=1
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
    except (requests.Timeout, requests.ConnectionError):
        raise

    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        status_code = e.response.status_code if e.response is not None else 0
        if status_code in (404, 410):
            log.warning(f"[posts] Страница недоступна ({status_code}): {url}")
            return None
        raise

    resp.encoding = "utf-8"

    try:
        soup = BeautifulSoup(resp.text, "html.parser")

        author_name = author_url = ""
        for a in soup.select("a[href*='/user/']"):
            name = a.get_text(strip=True)
            if name:
                author_name = name
                href = a.get("href", "")
                author_url = (
                    f"https://inkstory.net{href}" if href.startswith("/") else href
                )
                break

        if not author_name:
            log.warning(f"[posts] Не найден автор: {url}")
            return None

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
            log.warning(f"[posts] Не найден текст: {url}")

        return {"author_name": author_name, "author_url": author_url, "text": text}

    except Exception as e:
        log.error(f"[posts] Ошибка парсинга {url}: {e}")
        return None


def parse() -> list[int]:
    """
    Парсит все ссылки с Parsed=0, сохраняет посты в БД.
    Возвращает список новых post_id для постов участников.

    Назначение постов в очередь — на стороне вызывающего (scheduler.py).
    """
    day = _get_current_day()
    if not day:
        log.error("[posts] Нет активного дня в таблице days")
        return []

    links                  = _get_unparsed_links()
    verified_reviewer_urls = _get_verified_reviewer_urls()
    new_post_ids: list[int] = []
    parsed_urls: list[str]  = []

    log.info(f"[posts] Ссылок для парсинга: {len(links)}, день: {day}")

    for i, url in enumerate(links, 1):
        log.debug(f"[posts] [{i}/{len(links)}] {url}")

        try:
            post = _parse_page(url)
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
            log.warning(f"[posts] Временная ошибка, пропускаем: {url} — {e}")
            time.sleep(PAGE_PAUSE_POSTS)
            continue

        if not post:
            log.warning(f"[posts] Постоянная ошибка, помечаем Parsed=1: {url}")
            parsed_urls.append(url)
            continue

        is_reviewer = post["author_url"] in verified_reviewer_urls
        bot_words   = count_words(post["text"])

        if is_reviewer:
            log.info(f"[posts] Пост жюри — пропускаем очередь: {post['author_name']}")

        post_id = _save_post(
            url=url,
            author_name=post["author_name"],
            author_url=post["author_url"],
            text=post["text"],
            day=day,
            bot_words=bot_words,
            is_reviewer=is_reviewer,
        )

        if post_id is None and not is_reviewer:
            log.warning(f"[posts] Не удалось сохранить пост: {url}")
            continue

        log.info(f"[posts] Сохранён: {post['author_name']} — {url} ({bot_words} слов)")
        parsed_urls.append(url)

        if post_id:
            new_post_ids.append(post_id)

        time.sleep(PAGE_PAUSE_POSTS)

    _mark_links_parsed(parsed_urls)
    log.info(f"[posts] Помечено Parsed=1: {len(parsed_urls)}, новых постов: {len(new_post_ids)}")
    return new_post_ids
