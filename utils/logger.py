"""
utils/logger.py — настройка логгера

Пишет в консоль и в файл logs/app.log (ротация по дням, хранится 30 дней).
"""

import logging
import pathlib
from logging.handlers import TimedRotatingFileHandler

LOGS_DIR = pathlib.Path(__file__).parent.parent / "logs"


def setup_logger(name: str = "inkstory") -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    LOGS_DIR.mkdir(exist_ok=True)

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%d.%m.%Y %H:%M:%S",
    )

    file_handler = TimedRotatingFileHandler(
        filename=LOGS_DIR / "app.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d.log"
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger
