"""Тесты queue_manager — оба режима, просроченные посты, race condition."""
import sys, os, sqlite3, pathlib, tempfile, importlib
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB = _tmp.name
_tmp.close()

import utils.database as db_module
db_module.DB_PATH = pathlib.Path(_TMP_DB)


def _set_mode(mode: str, expire: int = 30):
    os.environ["QUEUE_MODE"]     = mode
    os.environ["EXPIRE_MINUTES"] = str(expire)
    import utils.config as cfg
    importlib.reload(cfg)
    import parser.queue_manager as qm
    importlib.reload(qm)
    return qm


from utils.database import get_db


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
            Author INTEGER NOT NULL,
            URL TEXT NOT NULL UNIQUE,
            Text TEXT, Day INTEGER,
            Status TEXT NOT NULL DEFAULT 'pending'
                CHECK(Status IN ('pending','checking','done','rejected','reviewer_post'))
        );
        CREATE TABLE queue (
            Post INTEGER NOT NULL PRIMARY KEY,
            Reviewer TEXT,
            AssignedAt TEXT NOT NULL DEFAULT (datetime('now','utc')),
            TakenAt TEXT
        );
        CREATE TABLE results (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            Post INTEGER NOT NULL UNIQUE,
            BotWords INTEGER, HumanWords INTEGER, HumanErrors INTEGER,
            RejectReason TEXT, Reviewer TEXT
        );

        INSERT INTO reviewers VALUES ('r1', 'https://inkstory.net/user/r1', 'Жюри1', 0, 1);
        INSERT INTO reviewers VALUES ('r2', 'https://inkstory.net/user/r2', 'Жюри2', 0, 1);
        INSERT INTO authors VALUES (1, 'Автор1', 'https://inkstory.net/user/a1');
        INSERT INTO days VALUES (1, '28.02.2026');
    """)
    conn.commit()
    conn.close()


def _add_post(post_id: int):
    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO posts_info (ID, Author, URL, Day, Status) VALUES (?,1,?,1,'pending')",
            (post_id, f"https://inkstory.net/p/{post_id}"),
        )
        db.execute("INSERT OR IGNORE INTO results (Post, BotWords) VALUES (?,100)", (post_id,))
        db.commit()


def test_distributed_assign():
    _init_test_db()
    qm = _set_mode("distributed")
    _add_post(1)
    tgid = qm.assign_post(1)
    assert tgid in ("r1", "r2"), f"got {tgid}"
    assert qm.get_queue_count(tgid) == 1


def test_distributed_balancing():
    _init_test_db()
    qm = _set_mode("distributed")
    qm.assign_post(_add_post(1) or 1)
    qm.assign_post(_add_post(2) or 2)
    assert qm.get_queue_count("r1") == 1
    assert qm.get_queue_count("r2") == 1


def test_take_post_distributed():
    _init_test_db()
    qm = _set_mode("distributed")
    _add_post(1)
    tgid = qm.assign_post(1)
    post = qm.take_post(tgid)
    assert post is not None and post["post_id"] == 1
    with get_db() as db:
        assert db.execute("SELECT Status FROM posts_info WHERE ID=1").fetchone()["Status"] == "checking"
        assert db.execute("SELECT TakenAt FROM queue WHERE Post=1").fetchone()["TakenAt"] is not None


def test_active_post_returns_same():
    _init_test_db()
    qm = _set_mode("distributed")
    _add_post(1)
    tgid = qm.assign_post(1)
    qm.take_post(tgid)
    assert qm.get_active_post(tgid)["post_id"] == 1


def test_open_assign_no_reviewer():
    _init_test_db()
    qm = _set_mode("open")
    _add_post(1)
    assert qm.assign_post(1) is None
    with get_db() as db:
        assert db.execute("SELECT Reviewer FROM queue WHERE Post=1").fetchone()["Reviewer"] is None


def test_open_first_reviewer_gets_post():
    _init_test_db()
    qm = _set_mode("open")
    _add_post(1)
    qm.assign_post(1)
    assert qm.take_post("r1")["post_id"] == 1
    assert qm.take_post("r2") is None


def test_open_free_count():
    _init_test_db()
    qm = _set_mode("open")
    _add_post(1); _add_post(2)
    qm.assign_post(1); qm.assign_post(2)
    assert qm.get_free_posts_count() == 2
    qm.take_post("r1")
    assert qm.get_free_posts_count() == 1


def test_release_post():
    _init_test_db()
    qm = _set_mode("distributed")
    _add_post(1)
    tgid = qm.assign_post(1)
    qm.take_post(tgid)
    qm.release_post(tgid, 1)
    with get_db() as db:
        assert db.execute("SELECT Status FROM posts_info WHERE ID=1").fetchone()["Status"] == "pending"
        q = db.execute("SELECT TakenAt, Reviewer FROM queue WHERE Post=1").fetchone()
        assert q["TakenAt"] is None and q["Reviewer"] is None


def test_remove_post():
    _init_test_db()
    qm = _set_mode("distributed")
    _add_post(1); qm.assign_post(1); qm.remove_post(1)
    with get_db() as db:
        assert db.execute("SELECT * FROM queue WHERE Post=1").fetchone() is None


def _set_taken_at(post_id: int, minutes_ago: int):
    old = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as db:
        db.execute("UPDATE queue SET TakenAt=? WHERE Post=?", (old, post_id))
        db.commit()


def test_expire_taken_post():
    _init_test_db()
    qm = _set_mode("distributed", expire=30)
    _add_post(1)
    tgid = qm.assign_post(1)
    qm.take_post(tgid)
    _set_taken_at(1, 31)
    released = qm.release_expired_posts()
    assert len(released) == 1 and released[0]["type"] == "taken"
    with get_db() as db:
        assert db.execute("SELECT Status FROM posts_info WHERE ID=1").fetchone()["Status"] == "pending"


def test_no_expire_for_fresh_post():
    _init_test_db()
    qm = _set_mode("distributed", expire=30)
    _add_post(1)
    tgid = qm.assign_post(1)
    qm.take_post(tgid)
    _set_taken_at(1, 10)
    assert len(qm.release_expired_posts()) == 0


def test_expire_assigned_not_taken():
    _init_test_db()
    qm = _set_mode("distributed", expire=30)
    _add_post(1)
    qm.assign_post(1)
    old = (datetime.now(timezone.utc) - timedelta(minutes=31)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as db:
        db.execute("UPDATE queue SET AssignedAt=? WHERE Post=1", (old,))
        db.commit()
    released = qm.release_expired_posts()
    assert all(r["type"] == "taken" for r in released)
    with get_db() as db:
        assert db.execute("SELECT Reviewer FROM queue WHERE Post=1").fetchone()["Reviewer"] is None


def test_total_queue_count():
    _init_test_db()
    qm = _set_mode("distributed")
    _add_post(1); _add_post(2); _add_post(3)
    qm.assign_post(1); qm.assign_post(2); qm.assign_post(3)
    assert qm.get_total_queue_count() == 3
    qm.take_post("r1")
    assert qm.get_total_queue_count() == 3
    qm.remove_post(1)
    assert qm.get_total_queue_count() == 2


if __name__ == "__main__":
    tests = [
        test_distributed_assign, test_distributed_balancing,
        test_take_post_distributed, test_active_post_returns_same,
        test_open_assign_no_reviewer, test_open_first_reviewer_gets_post,
        test_open_free_count, test_release_post, test_remove_post,
        test_expire_taken_post, test_no_expire_for_fresh_post,
        test_expire_assigned_not_taken, test_total_queue_count,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            passed += 1
        except Exception as e:
            import traceback
            print(f"  ❌ {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{'='*40}\nПройдено: {passed}/{passed+failed}")
    try:
        os.unlink(_TMP_DB)
    except Exception:
        pass
    if failed:
        sys.exit(1)
