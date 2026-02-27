"""
links.py — сбор ссылок на посты через JSON API inkstory.net

Логика:
1. Берём все известные ссылки из таблицы links
2. Постранично запрашиваем API
3. Останавливаемся когда встречаем ссылку которая уже есть в БД
4. Новые ссылки сохраняем с Parsed = 0
"""

import time
import requests

from utils.database import get_db
from utils.logger import setup_logger

log = setup_logger()

API_URL    = "https://api.inkstory.net/v2/discussions"
SITE_BASE  = "https://inkstory.net"
PAGE_SIZE  = 20
PAGE_PAUSE = 1.0  # секунды между запросами к API

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; Raspberry Pi) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://inkstory.net/",
}


# ── БД ────────────────────────────────────────────────────────────────────────

def _get_known_links() -> set[str]:
    """Возвращает все ссылки из таблицы links — используется как стоп-сигнал."""
    with get_db() as db:
        rows = db.execute("SELECT URL FROM links").fetchall()
        return {row["URL"] for row in rows}


def _get_blacklist() -> set[str]:
    with get_db() as db:
        rows = db.execute("SELECT URL FROM blacklist").fetchall()
        return {row["URL"] for row in rows}


def _save_links(urls: list[str]) -> None:
    """Сохраняет новые ссылки с Parsed = 0."""
    with get_db() as db:
        db.executemany(
            "INSERT OR IGNORE INTO links (URL, Parsed) VALUES (?, 0)",
            [(url,) for url in urls],
        )
        db.commit()


# ── API ───────────────────────────────────────────────────────────────────────

def _fetch_page(page: int) -> dict | list | None:
    """Загружает одну страницу API. Возвращает None при ошибке."""
    params = {
        "size": PAGE_SIZE,
        "sort": "createdAt,desc",
        "page": page,
        "includeContent": "true",
    }
    try:
        response = requests.get(API_URL, params=params, headers=HEADERS, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        log.error(f"[links] Ошибка запроса страницы {page}: {e}")
        return None
    except ValueError as e:
        log.error(f"[links] Ошибка парсинга JSON страницы {page}: {e}")
        return None


def _extract_links(data: dict | list) -> tuple[list[str], bool]:
    """
    Извлекает ссылки из ответа API.
    Возвращает (список ссылок, есть ли следующая страница).
    """
    if isinstance(data, list):
        items = data
        has_next = len(data) == PAGE_SIZE
    else:
        items = data.get("content") or data.get("data") or data.get("items") or []

        if "last" in data:
            has_next = not data["last"]
        elif "hasNext" in data:
            has_next = data["hasNext"]
        elif "nextPage" in data:
            has_next = data["nextPage"] is not None
        else:
            has_next = len(items) == PAGE_SIZE

    urls = []
    for item in items:
        slug = item.get("slug") or item.get("id") or item.get("uuid")
        if slug:
            urls.append(f"{SITE_BASE}/discussion/{slug}")

    return urls, has_next


# ── Основная функция ──────────────────────────────────────────────────────────

def parse() -> int:
    """
    Собирает новые ссылки и сохраняет их в БД с Parsed = 0.
    Останавливается когда встречает ссылку которая уже есть в links.
    Возвращает количество новых сохранённых ссылок.
    """
    known   = _get_known_links()
    blacklist = _get_blacklist()
    log.info(f"[links] Известных ссылок: {len(known)}, в блэклисте: {len(blacklist)}")

    new_urls = []
    page     = 0
    stop     = False

    while not stop:
        log.debug(f"[links] Запрашиваем страницу {page}")
        data = _fetch_page(page)

        if data is None:
            log.warning(f"[links] Не удалось получить страницу {page}, останавливаемся")
            break

        page_urls, has_next = _extract_links(data)

        if not page_urls:
            log.info(f"[links] Страница {page} пустая — конец")
            break

        for url in page_urls:
            if url in blacklist:
                log.debug(f"[links] Блэклист: {url}")
                continue
            if url in known:
                log.info(f"[links] Дошли до известной ссылки: {url}")
                stop = True
                break
            if url not in new_urls:
                new_urls.append(url)

        if not stop and has_next:
            page += 1
            time.sleep(PAGE_PAUSE)
        else:
            break

    if new_urls:
        _save_links(new_urls)
        log.info(f"[links] Сохранено новых ссылок: {len(new_urls)}")
    else:
        log.info("[links] Новых ссылок нет")

    return len(new_urls)