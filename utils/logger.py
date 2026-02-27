"""
logger.py — настройка логгера

Пишет в консоль и в файл logs/YYYY-MM-DD.log
Каждый день создаётся новый файл, старые хранятся 30 дней
"""

import logging
import pathlib
from logging.handlers import TimedRotatingFileHandler

LOGS_DIR = pathlib.Path(__file__).parent.parent / "logs"


def setup_logger(name: str = "inkstory") -> logging.Logger:
    logger = logging.getLogger(name)

    # Не добавляем хендлеры повторно если логгер уже настроен
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    LOGS_DIR.mkdir(exist_ok=True)
    log_file = LOGS_DIR / "app.log"

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%d.%m.%Y %H:%M:%S",
    )

    # Файл — новый каждый день в полночь, храним 30 дней
    file_handler = TimedRotatingFileHandler(
        filename=log_file,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d.log"
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    # Консоль — только INFO и выше чтобы не спамить на Pi
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger