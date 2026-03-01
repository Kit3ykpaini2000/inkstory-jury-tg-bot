"""Тесты utils/config.py — чтение настроек из окружения."""
import sys, os, pathlib, importlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# Сохраняем оригинальный BOT_TOKEN (на Pi он есть в .env, на CI — нет)
_REAL_TOKEN = os.environ.get("BOT_TOKEN", "")


def _reload():
    """Перезагружает config, временно блокируя load_dotenv чтобы .env не перезаписал os.environ."""
    import unittest.mock as mock
    import utils.config as cfg
    with mock.patch("utils.config.load_dotenv"):
        importlib.reload(cfg)
    return cfg


def test_defaults():
    """Без явных переменных дефолты должны применяться."""
    os.environ.pop("QUEUE_MODE",      None)
    os.environ.pop("EXPIRE_MINUTES",  None)
    os.environ.pop("PARSER_INTERVAL", None)
    os.environ.pop("MAX_WORDS",       None)
    os.environ.pop("MAX_ERRORS",      None)
    os.environ.pop("AI_CHUNK_SIZE",   None)
    os.environ.pop("GROQ_MODEL",      None)

    cfg = _reload()

    assert cfg.QUEUE_MODE      == "distributed"
    assert cfg.EXPIRE_MINUTES  == 30
    assert cfg.PARSER_INTERVAL == 1800
    assert cfg.MAX_WORDS       == 100_000
    assert cfg.MAX_ERRORS      == 10_000
    assert cfg.AI_CHUNK_SIZE   == 3000
    assert cfg.GROQ_MODEL      == "llama-3.3-70b-versatile"


def test_env_override():
    """os.environ должен переопределять дефолты."""
    os.environ["QUEUE_MODE"]     = "open"
    os.environ["EXPIRE_MINUTES"] = "45"
    os.environ["MAX_WORDS"]      = "50000"
    cfg = _reload()

    assert cfg.QUEUE_MODE     == "open"
    assert cfg.EXPIRE_MINUTES == 45
    assert cfg.MAX_WORDS      == 50_000

    del os.environ["QUEUE_MODE"]
    del os.environ["EXPIRE_MINUTES"]
    del os.environ["MAX_WORDS"]


def test_validate_no_token():
    """validate() должен падать если BOT_TOKEN пустой."""
    os.environ["BOT_TOKEN"] = ""
    cfg = _reload()
    try:
        cfg.validate()
        assert False, "Должен был упасть ValueError"
    except ValueError as e:
        assert "BOT_TOKEN" in str(e)
    finally:
        # Восстанавливаем реальный токен
        if _REAL_TOKEN:
            os.environ["BOT_TOKEN"] = _REAL_TOKEN
        else:
            os.environ.pop("BOT_TOKEN", None)


def test_validate_bad_queue_mode():
    """validate() должен падать при неверном QUEUE_MODE."""
    os.environ["BOT_TOKEN"]  = "fake:token"
    os.environ["QUEUE_MODE"] = "invalid_mode"
    cfg = _reload()

    try:
        cfg.validate()
        assert False, "Должен был упасть ValueError"
    except ValueError as e:
        assert "QUEUE_MODE" in str(e)
    finally:
        del os.environ["QUEUE_MODE"]
        if _REAL_TOKEN:
            os.environ["BOT_TOKEN"] = _REAL_TOKEN
        else:
            os.environ.pop("BOT_TOKEN", None)


def test_validate_ok():
    """validate() не должен падать при правильных настройках."""
    os.environ["BOT_TOKEN"]  = "123:fake_token"
    os.environ["QUEUE_MODE"] = "balanced"
    cfg = _reload()
    cfg.validate()  # не должен бросить исключение

    del os.environ["QUEUE_MODE"]
    if _REAL_TOKEN:
        os.environ["BOT_TOKEN"] = _REAL_TOKEN
    else:
        os.environ.pop("BOT_TOKEN", None)


def test_parser_interval_seconds():
    """PARSER_INTERVAL должен быть в секундах (минуты * 60)."""
    os.environ["PARSER_INTERVAL"] = "10"
    cfg = _reload()
    assert cfg.PARSER_INTERVAL == 600
    del os.environ["PARSER_INTERVAL"]


if __name__ == "__main__":
    tests = [
        test_defaults,
        test_env_override,
        test_validate_no_token,
        test_validate_bad_queue_mode,
        test_validate_ok,
        test_parser_interval_seconds,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {t.__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1
    print(f"\nПройдено: {passed}/{passed+failed}")
    if failed:
        sys.exit(1)

