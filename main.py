import sys as _sys
import pathlib as _pathlib
_ROOT = _pathlib.Path(__file__).parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

"""
main.py — точка входа парсера

Запуск: python main.py

Шаги:
1. Сбор новых ссылок (links.py)
2. Парсинг постов по ссылкам (posts.py)
"""

import sys
import pathlib

from parser.links import parse as parse_links
from parser.posts import parse as parse_posts
from utils.logger import setup_logger

log = setup_logger()


def main() -> None:
    log.info("=" * 50)
    log.info("Запуск парсера inkstory.net")
    log.info("=" * 50)

    log.info("--- Шаг 1: сбор ссылок ---")
    new_links = parse_links()

    log.info("--- Шаг 2: парсинг постов ---")
    new_posts = parse_posts()

    log.info("=" * 50)
    log.info(f"Готово. Новых ссылок: {new_links}, постов в очереди: {new_posts}")
    log.info("=" * 50)


if __name__ == "__main__":
    main()