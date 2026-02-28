"""Тесты queue_manager — оба режима, просроченные посты, race condition."""
import sys
import sqlite3
import pathlib
import tempfile
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# Используем временную БД для тестов
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB = _tmp.name
_tmp.close()

import utils.database as db_module
db_module.DB_PATH = pathlib.Path(_TMP_DB)

from utils.database import get_db, set_config
from parser.queue_manager import (
    assign_post, take_post, get_active_post, release_post,
    remove_post, release_expired_posts, get_queue_count,
    get_total_queue_count, get_free_posts_count,
)


def _init_test_db():
    conn = sqlite3.connect(_TMP_DB)
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        DROP TABLE IF EXISTS queue;
        DROP TABLE IF EXISTS results;
        DROP TABLE IF EXISTS posts_info;
        DROP TABLE IF EXISTS authors;
        DROP TABLE IF EXISTS days;
        DROP TABLE IF EXISTS reviewers;
        DROP TABLE IF EXISTS config;

        CREATE TABLE reviewers (
            TGID TEXT PRIMARY KEY, URL TEXT NOT NULL UNIQUE,
            Name TEXT NOT NULL, IsAdmin INTEGER DEFAULT 0, Verified INTEGER DEFAULT 0
        );
        CREATE TABLE authors (
            ID INTEGER PRIMARY KEY AUTOINCREMENT, Name TEXT NOT NULL, URL TEXT NOT NULL UNIQUE
        );
        CREATE TABLE days (
            Day INTEGER PRIMARY KEY AUTOINCREMENT, Data TEXT NOT NULL
        );
        CREATE TABLE posts_info (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            Author INTEGER NOT NULL REFERENCES authors(ID),
            URL TEXT NOT NULL UNIQUE,
            Text TEXT,
            Day INTEGER,
            Status TEXT NOT NULL DEFAULT 'pending'
                CHECK(Status IN ('pending','checking','done','rejected','reviewer_post'))
        );
        CREATE TABLE queue (
            Post INTEGER NOT NULL PRIMARY KEY REFERENCES posts_info(ID),
            Reviewer TEXT REFERENCES reviewers(TGID),
            AssignedAt TEXT NOT NULL DEFAULT (datetime('now','utc')),
            TakenAt TEXT
        );
        CREATE TABLE results (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            Post INTEGER NOT NULL UNIQUE REFERENCES posts_info(ID),
            BotWords INTEGER, HumanWords INTEGER, HumanErrors INTEGER,
            RejectReason TEXT, Reviewer TEXT
        );
        CREATE TABLE config (
            Key TEXT PRIMARY KEY, Value TEXT NOT NULL
        );

        INSERT INTO config VALUES ('queue_mode', 'distributed');
        INSERT INTO config VALUES ('expire_minutes', '30');

        INSERT INTO reviewers VALUES ('r1', 'https://inkstory.net/user/r1', 'Жюри1', 0, 1);
        INSERT INTO reviewers VALUES ('r2', 'https://inkstory.net/user/r2', 'Жюри2', 0, 1);
        INSERT INTO authors VALUES (1, 'Автор1', 'https://inkstory.net/user/a1');
        INSERT INTO days VALUES (1, '28.02.2026');
    """)
    conn.commit()
    conn.close()


def _add_post(post_id: int, url: str = None) -> int:
    url = url or f"https://inkstory.net/p/{post_id}"
    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO posts_info (ID, Author, URL, Day, Status) VALUES (?,1,?,1,'pending')",
            (post_id, url),
        )
        db.execute(
            "INSERT OR IGNORE INTO results (Post, BotWords) VALUES (?,100)",
            (post_id,),
        )
        db.commit()
    return post_id


# ══════════════════════════════════════════════════════════════════════════════
# Тесты режима distributed
# ══════════════════════════════════════════════════════════════════════════════

def test_distributed_assign():
    _init_test_db()
    set_config("queue_mode", "distributed")

    post_id = _add_post(1)
    tgid    = assign_post(post_id)
    assert tgid in ("r1", "r2"), f"Ожидался r1 или r2, получили {tgid}"
    assert get_queue_count(tgid) == 1


def test_distributed_balancing():
    """Два поста должны уйти разным жюри."""
    _init_test_db()
    set_config("queue_mode", "distributed")

    assign_post(_add_post(1))
    assign_post(_add_post(2))

    c1 = get_queue_count("r1")
    c2 = get_queue_count("r2")
    assert c1 == 1 and c2 == 1, f"Ожидалось по 1, получили r1={c1} r2={c2}"


def test_take_post_distributed():
    """Жюри берёт пост через /next."""
    _init_test_db()
    set_config("queue_mode", "distributed")

    post_id = _add_post(1)
    tgid    = assign_post(post_id)

    post = take_post(tgid)
    assert post is not None
    assert post["post_id"] == post_id

    # Статус должен смениться на checking
    with get_db() as db:
        row = db.execute("SELECT Status FROM posts_info WHERE ID=?", (post_id,)).fetchone()
    assert row["Status"] == "checking"

    # TakenAt должен быть выставлен
    with get_db() as db:
        row = db.execute("SELECT TakenAt FROM queue WHERE Post=?", (post_id,)).fetchone()
    assert row["TakenAt"] is not None


def test_active_post_returns_same():
    """Повторный /next возвращает тот же пост."""
    _init_test_db()
    set_config("queue_mode", "distributed")

    post_id = _add_post(1)
    tgid    = assign_post(post_id)

    take_post(tgid)
    active = get_active_post(tgid)
    assert active is not None
    assert active["post_id"] == post_id


# ══════════════════════════════════════════════════════════════════════════════
# Тесты режима open
# ══════════════════════════════════════════════════════════════════════════════

def test_open_assign_no_reviewer():
    """В режиме open пост кладётся без Reviewer."""
    _init_test_db()
    set_config("queue_mode", "open")

    post_id = _add_post(1)
    result  = assign_post(post_id)
    assert result is None  # open режим не назначает жюри

    with get_db() as db:
        row = db.execute("SELECT Reviewer FROM queue WHERE Post=?", (post_id,)).fetchone()
    assert row["Reviewer"] is None


def test_open_first_reviewer_gets_post():
    """В режиме open первый кто вызывает take_post получает пост."""
    _init_test_db()
    set_config("queue_mode", "open")

    post_id = _add_post(1)
    assign_post(post_id)

    post = take_post("r1")
    assert post is not None
    assert post["post_id"] == post_id

    # r2 не должен получить этот пост
    post2 = take_post("r2")
    assert post2 is None


def test_open_free_count():
    _init_test_db()
    set_config("queue_mode", "open")

    _add_post(1)
    _add_post(2)
    assign_post(1)
    assign_post(2)

    assert get_free_posts_count() == 2
    take_post("r1")
    assert get_free_posts_count() == 1


# ══════════════════════════════════════════════════════════════════════════════
# Тесты release и remove
# ══════════════════════════════════════════════════════════════════════════════

def test_release_post():
    """После release пост снова доступен."""
    _init_test_db()
    set_config("queue_mode", "distributed")

    post_id = _add_post(1)
    tgid    = assign_post(post_id)
    take_post(tgid)
    release_post(tgid, post_id)

    with get_db() as db:
        row = db.execute("SELECT Status FROM posts_info WHERE ID=?", (post_id,)).fetchone()
        q   = db.execute("SELECT TakenAt, Reviewer FROM queue WHERE Post=?", (post_id,)).fetchone()

    assert row["Status"] == "pending"
    assert q["TakenAt"] is None
    assert q["Reviewer"] is None


def test_remove_post():
    """После remove поста нет в очереди."""
    _init_test_db()
    set_config("queue_mode", "distributed")

    post_id = _add_post(1)
    assign_post(post_id)
    remove_post(post_id)

    with get_db() as db:
        row = db.execute("SELECT * FROM queue WHERE Post=?", (post_id,)).fetchone()
    assert row is None


# ══════════════════════════════════════════════════════════════════════════════
# Тесты просроченных постов
# ══════════════════════════════════════════════════════════════════════════════

def _set_taken_at(post_id: int, minutes_ago: int):
    old_time = (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    ).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as db:
        db.execute("UPDATE queue SET TakenAt=? WHERE Post=?", (old_time, post_id))
        db.commit()


def test_expire_taken_post():
    """Пост взятый 31 минуту назад должен быть освобождён."""
    _init_test_db()
    set_config("queue_mode", "distributed")
    set_config("expire_minutes", "30")

    post_id = _add_post(1)
    tgid    = assign_post(post_id)
    take_post(tgid)
    _set_taken_at(post_id, 31)

    released = release_expired_posts()
    assert len(released) == 1
    assert released[0]["post_id"] == post_id
    assert released[0]["reviewer_tgid"] == tgid
    assert released[0]["type"] == "taken"

    with get_db() as db:
        row = db.execute("SELECT Status FROM posts_info WHERE ID=?", (post_id,)).fetchone()
        q   = db.execute("SELECT TakenAt, Reviewer FROM queue WHERE Post=?", (post_id,)).fetchone()
    assert row["Status"] == "pending"
    assert q["TakenAt"] is None
    assert q["Reviewer"] is None


def test_no_expire_for_fresh_post():
    """Пост взятый 10 минут назад не должен освобождаться."""
    _init_test_db()
    set_config("queue_mode", "distributed")
    set_config("expire_minutes", "30")

    post_id = _add_post(1)
    tgid    = assign_post(post_id)
    take_post(tgid)
    _set_taken_at(post_id, 10)

    released = release_expired_posts()
    assert len(released) == 0


def test_expire_assigned_not_taken():
    """Пост назначен (distributed) но не взят за 31 мин — Reviewer=NULL."""
    _init_test_db()
    set_config("queue_mode", "distributed")
    set_config("expire_minutes", "30")

    post_id = _add_post(1)
    tgid    = assign_post(post_id)

    # Симулируем старый AssignedAt
    old_time = (
        datetime.now(timezone.utc) - timedelta(minutes=31)
    ).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as db:
        db.execute("UPDATE queue SET AssignedAt=? WHERE Post=?", (old_time, post_id))
        db.commit()

    released = release_expired_posts()
    # type=assigned не попадает в released (нет уведомления)
    assert all(r["type"] == "taken" for r in released)

    with get_db() as db:
        q = db.execute("SELECT Reviewer FROM queue WHERE Post=?", (post_id,)).fetchone()
    assert q["Reviewer"] is None


# ══════════════════════════════════════════════════════════════════════════════
# Тест total_queue_count
# ══════════════════════════════════════════════════════════════════════════════

def test_total_queue_count():
    _init_test_db()
    set_config("queue_mode", "distributed")

    _add_post(1); _add_post(2); _add_post(3)
    assign_post(1); assign_post(2); assign_post(3)

    assert get_total_queue_count() == 3

    tgid = "r1"
    take_post(tgid)
    # После take_post статус 'checking' — всё ещё в очереди
    assert get_total_queue_count() == 3

    remove_post(1)
    assert get_total_queue_count() == 2


# ── Запуск ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_distributed_assign,
        test_distributed_balancing,
        test_take_post_distributed,
        test_active_post_returns_same,
        test_open_assign_no_reviewer,
        test_open_first_reviewer_gets_post,
        test_open_free_count,
        test_release_post,
        test_remove_post,
        test_expire_taken_post,
        test_no_expire_for_fresh_post,
        test_expire_assigned_not_taken,
        test_total_queue_count,
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

    print(f"\n{'='*40}")
    print(f"Пройдено: {passed}/{passed+failed}")

    # Удаляем временную БД
    try:
        os.unlink(_TMP_DB)
    except Exception:
        pass

    if failed:
        sys.exit(1)
