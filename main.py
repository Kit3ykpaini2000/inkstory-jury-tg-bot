import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))

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
    log.info(f"Готово. Новых ссылок: {new_links}, постов назначено: {sum(new_posts.values())}")
    log.info("=" * 50)


if __name__ == "__main__":
    main()
