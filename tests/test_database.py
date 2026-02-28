"""Тесты utils/database.py — config хелперы."""
import sys
import sqlite3
import pathlib
import tempfile
import os

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB = _tmp.name
_tmp.close()

import utils.database as db_module
db_module.DB_PATH = pathlib.Path(_TMP_DB)

from utils.database import get_db, get_config, set_config, get_queue_mode, get_expire_minutes


def _init():
    conn = sqlite3.connect(_TMP_DB)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS config (Key TEXT PRIMARY KEY, Value TEXT NOT NULL);
        INSERT OR IGNORE INTO config VALUES ('queue_mode', 'distributed');
        INSERT OR IGNORE INTO config VALUES ('expire_minutes', '30');
    """)
    conn.commit()
    conn.close()


def test_get_config():
    _init()
    assert get_config("queue_mode") == "distributed"
    assert get_config("expire_minutes") == "30"


def test_get_config_default():
    _init()
    assert get_config("nonexistent", "fallback") == "fallback"
    assert get_config("nonexistent") is None


def test_set_config():
    _init()
    set_config("queue_mode", "open")
    assert get_config("queue_mode") == "open"
    # Возвращаем обратно
    set_config("queue_mode", "distributed")


def test_get_queue_mode():
    _init()
    set_config("queue_mode", "distributed")
    assert get_queue_mode() == "distributed"

    set_config("queue_mode", "open")
    assert get_queue_mode() == "open"


def test_get_expire_minutes():
    _init()
    assert get_expire_minutes() == 30

    set_config("expire_minutes", "45")
    assert get_expire_minutes() == 45

    set_config("expire_minutes", "30")


def test_set_config_upsert():
    """set_config должен обновлять существующее значение."""
    _init()
    set_config("queue_mode", "open")
    set_config("queue_mode", "distributed")
    assert get_config("queue_mode") == "distributed"


if __name__ == "__main__":
    tests = [
        test_get_config,
        test_get_config_default,
        test_set_config,
        test_get_queue_mode,
        test_get_expire_minutes,
        test_set_config_upsert,
    ]

    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {t.__name__}: {e}")
            failed += 1

    print(f"\nПройдено: {passed}/{passed+failed}")

    try:
        os.unlink(_TMP_DB)
    except Exception:
        pass

    if failed:
        sys.exit(1)
