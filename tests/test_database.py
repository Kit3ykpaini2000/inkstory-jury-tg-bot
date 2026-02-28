"""Тесты utils/config.py — чтение настроек из окружения."""
import sys, os, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))


def test_defaults():
    """Без .env должны вернуться дефолтные значения."""
    os.environ.pop("QUEUE_MODE",     None)
    os.environ.pop("EXPIRE_MINUTES", None)
    os.environ.pop("PARSER_INTERVAL",None)
    os.environ.pop("MAX_WORDS",      None)
    os.environ.pop("MAX_ERRORS",     None)
    os.environ.pop("AI_CHUNK_SIZE",  None)
    os.environ.pop("GROQ_MODEL",     None)

    # Перезагружаем модуль чтобы дефолты применились
    import importlib, utils.config as cfg
    importlib.reload(cfg)

    assert cfg.QUEUE_MODE       == "distributed"
    assert cfg.EXPIRE_MINUTES   == 30
    assert cfg.PARSER_INTERVAL  == 1800   # 30 мин * 60
    assert cfg.MAX_WORDS        == 100_000
    assert cfg.MAX_ERRORS       == 10_000
    assert cfg.AI_CHUNK_SIZE    == 3000
    assert cfg.GROQ_MODEL       == "llama-3.3-70b-versatile"


def test_env_override():
    """Переменные окружения должны переопределять дефолты."""
    import importlib, utils.config as cfg

    os.environ["QUEUE_MODE"]     = "open"
    os.environ["EXPIRE_MINUTES"] = "45"
    os.environ["MAX_WORDS"]      = "50000"
    importlib.reload(cfg)

    assert cfg.QUEUE_MODE     == "open"
    assert cfg.EXPIRE_MINUTES == 45
    assert cfg.MAX_WORDS      == 50_000

    # Чистим
    del os.environ["QUEUE_MODE"]
    del os.environ["EXPIRE_MINUTES"]
    del os.environ["MAX_WORDS"]
    importlib.reload(cfg)


def test_validate_no_token():
    """validate() должен падать если BOT_TOKEN пустой."""
    import importlib, utils.config as cfg

    os.environ.pop("BOT_TOKEN", None)
    importlib.reload(cfg)

    try:
        cfg.validate()
        assert False, "Должен был упасть ValueError"
    except ValueError as e:
        assert "BOT_TOKEN" in str(e)


def test_validate_bad_queue_mode():
    """validate() должен падать при неверном QUEUE_MODE."""
    import importlib, utils.config as cfg

    os.environ["BOT_TOKEN"]  = "fake"
    os.environ["QUEUE_MODE"] = "invalid_mode"
    importlib.reload(cfg)

    try:
        cfg.validate()
        assert False, "Должен был упасть ValueError"
    except ValueError as e:
        assert "QUEUE_MODE" in str(e)

    del os.environ["QUEUE_MODE"]
    del os.environ["BOT_TOKEN"]
    importlib.reload(cfg)


def test_validate_ok():
    """validate() не должен падать при правильных настройках."""
    import importlib, utils.config as cfg

    os.environ["BOT_TOKEN"]  = "123:fake_token"
    os.environ["QUEUE_MODE"] = "open"
    importlib.reload(cfg)

    cfg.validate()  # не должен бросить исключение

    del os.environ["BOT_TOKEN"]
    del os.environ["QUEUE_MODE"]
    importlib.reload(cfg)


def test_parser_interval_seconds():
    """PARSER_INTERVAL должен быть в секундах (минуты * 60)."""
    import importlib, utils.config as cfg

    os.environ["PARSER_INTERVAL"] = "10"
    importlib.reload(cfg)
    assert cfg.PARSER_INTERVAL == 600

    del os.environ["PARSER_INTERVAL"]
    importlib.reload(cfg)


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
            failed += 1
    print(f"\nПройдено: {passed}/{passed+failed}")
    if failed:
        sys.exit(1)
