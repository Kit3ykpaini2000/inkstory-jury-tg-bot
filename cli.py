"""
cli.py — консольное управление inkstory-bot

Запуск: python cli.py

Включает: статистику, управление жюри/постами/днями/ссылками,
          просмотр логов, инициализацию БД, экспорт результатов.
"""

import os
import sys
import pathlib
import sqlite3
from datetime import datetime

ROOT = pathlib.Path(__file__).parent

from utils.database import get_db

try:
    from parser.queue_manager import (
        get_total_queue_count, get_queue_count, assign_post,
        remove_post, release_post, get_all_reviewer_queue_sizes,
    )
    _QM = True
except ImportError:
    _QM = False

# ── Цвета ─────────────────────────────────────────────────────────────────────

R     = "\033[91m"
G     = "\033[92m"
Y     = "\033[93m"
B     = "\033[94m"
C     = "\033[96m"
W     = "\033[97m"
DIM   = "\033[2m"
BOLD  = "\033[1m"
RESET = "\033[0m"


def clr():
    os.system("cls" if os.name == "nt" else "clear")


def hr(char="─", color=DIM):
    try:
        w = os.get_terminal_size().columns
    except OSError:
        w = 80
    print(f"{color}{char * w}{RESET}")


def pause():
    input(f"\n{DIM}[ Enter — назад ]{RESET}")


def confirm(msg: str) -> bool:
    ans = input(f"{Y}⚠  {msg} [y/N]: {RESET}").strip().lower()
    return ans == "y"


def header(title: str, show_stats: bool = False):
    clr()
    hr("═", B)
    now = datetime.now().strftime("%d.%m.%Y  %H:%M")
    print(f"{BOLD}{C}  InkStory CLI  {DIM}│{RESET}{BOLD}  {title}  {DIM}│  {now}{RESET}")
    hr("═", B)
    if show_stats:
        print(_quick_stats())
        hr()
    print()


def menu(title: str, options: list, show_stats: bool = False) -> int:
    header(title, show_stats=show_stats)
    for i, opt in enumerate(options, 1):
        print(f"  {BOLD}{C}{i}{RESET}. {opt}")
    print(f"  {DIM}0. ← Назад / Выход{RESET}")
    print()
    while True:
        try:
            raw = input(f"{W}Выбор: {RESET}").strip()
            if raw == "0":
                return -1
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return idx
        except (ValueError, EOFError):
            pass
        print(f"{R}  Введите число от 0 до {len(options)}{RESET}")


def _quick_stats() -> str:
    try:
        with get_db() as db:
            done = db.execute(
                "SELECT COUNT(*) FROM posts_info WHERE Status='done'"
            ).fetchone()[0]
            checking = db.execute(
                "SELECT COUNT(*) FROM posts_info WHERE Status='checking'"
            ).fetchone()[0]
        in_queue = get_total_queue_count() if _QM else 0
        checking_str = f"  {Y}🔒 Проверяется: {checking}{RESET}" if checking else ""
        return (
            f"  {B}📋 В очереди: {in_queue}{RESET}   "
            f"{G}✅ Проверено: {done}{RESET}"
            f"{checking_str}"
        )
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# СТАТИСТИКА
# ══════════════════════════════════════════════════════════════════════════════

def show_stats():
    header("📊 Статистика")
    with get_db() as db:
        in_queue  = get_total_queue_count() if _QM else 0
        pending   = db.execute("SELECT COUNT(*) FROM posts_info WHERE Status='pending'").fetchone()[0]
        checking  = db.execute("SELECT COUNT(*) FROM posts_info WHERE Status='checking'").fetchone()[0]
        done      = db.execute("SELECT COUNT(*) FROM posts_info WHERE Status='done'").fetchone()[0]
        rejected  = db.execute("SELECT COUNT(*) FROM posts_info WHERE Status='rejected'").fetchone()[0]
        total     = db.execute(
            "SELECT COUNT(*) FROM posts_info WHERE Status NOT IN ('reviewer_post')"
        ).fetchone()[0]
        reviewers = db.execute("SELECT COUNT(*) FROM reviewers").fetchone()[0]
        links     = db.execute("SELECT COUNT(*) FROM links").fetchone()[0]

        rows = db.execute(
            """
            SELECT rv.Name,
                   COUNT(CASE WHEN r.HumanWords IS NOT NULL THEN 1 END) AS checked,
                   COUNT(CASE WHEN p.Status='rejected' AND r.Reviewer=rv.TGID THEN 1 END) AS rejected
            FROM reviewers rv
            LEFT JOIN results    r ON r.Reviewer = rv.TGID
            LEFT JOIN posts_info p ON p.ID = r.Post
            GROUP BY rv.TGID
            ORDER BY checked DESC
            """
        ).fetchall()

    print(f"  {Y}⏳ Ожидает проверки:{RESET}  {pending}")
    print(f"  {Y}🔒 Проверяется:{RESET}        {checking}")
    print(f"  {G}✅ Проверено:{RESET}          {done}")
    print(f"  {R}❌ Отклонено:{RESET}          {rejected}")
    print(f"  {W}📦 Всего постов:{RESET}       {total}")
    print(f"  {B}📋 В очереди:{RESET}          {in_queue}")
    print(f"  {Y}🔗 Ссылок в БД:{RESET}        {links}")
    print(f"  {W}👥 Жюри:{RESET}               {reviewers}")

    if rows:
        print()
        hr()
        print(f"  {BOLD}Жюри — рейтинг:{RESET}")
        print(f"  {'Имя':<22} {'Проверено':>10} {'Отклонено':>10}")
        hr()
        for i, r in enumerate(rows, 1):
            bar = "█" * min(r["checked"], 30)
            print(f"  {i:2}. {r['Name']:<20} {C}{bar}{RESET} {r['checked']}  ❌{r['rejected']}")

    pause()


# ══════════════════════════════════════════════════════════════════════════════
# ЖЮРИ
# ══════════════════════════════════════════════════════════════════════════════

def manage_reviewers():
    actions = [
        list_reviewers, add_reviewer,
        lambda: set_verified(1), lambda: set_verified(0),
        lambda: set_admin(1), lambda: set_admin(0),
        delete_reviewer,
    ]
    while True:
        choice = menu("👥 Управление жюри", [
            "Список жюри",
            "Добавить жюри",
            "Верифицировать жюри",
            "Снять верификацию",
            "Сделать администратором",
            "Снять права администратора",
            "Удалить жюри",
        ])
        if choice == -1:
            return
        actions[choice]()


def list_reviewers():
    header("👥 Список жюри")
    with get_db() as db:
        rows = db.execute(
            "SELECT TGID, Name, URL, IsAdmin, Verified FROM reviewers ORDER BY Name"
        ).fetchall()
    if not rows:
        print(f"  {DIM}Жюри не зарегистрированы.{RESET}")
    else:
        print(f"  {'TGID':<15} {'Имя':<22} {'Верф':4} {'Адм':4}")
        hr()
        for r in rows:
            adm  = f"{Y}★{RESET}" if r["IsAdmin"]  else " "
            ver  = f"{G}✓{RESET}" if r["Verified"] else f"{R}✗{RESET}"
            print(f"  {r['TGID']:<15} {r['Name']:<22} {ver}    {adm}")
    pause()


def _pick_reviewer(prompt: str = "Выберите жюри") -> str | None:
    with get_db() as db:
        rows = db.execute("SELECT TGID, Name FROM reviewers ORDER BY Name").fetchall()
    if not rows:
        print(f"  {R}Жюри не найдены.{RESET}")
        pause()
        return None
    opts = [f"{r['Name']}  (tgID: {r['TGID']})" for r in rows]
    idx = menu(prompt, opts)
    if idx == -1:
        return None
    return rows[idx]["TGID"]


def add_reviewer():
    header("➕ Добавить жюри")
    name   = input(f"  Имя (никнейм): {W}").strip(); print(RESET, end="")
    tg_id  = input(f"  Telegram ID:   {W}").strip(); print(RESET, end="")
    url    = input(f"  URL профиля:   {W}").strip(); print(RESET, end="")
    is_adm = input(f"  Администратор? [y/N]: {W}").strip().lower() == "y"; print(RESET, end="")

    if not name or not tg_id:
        print(f"{R}  Имя и tgID обязательны.{RESET}"); pause(); return

    with get_db() as db:
        try:
            db.execute(
                "INSERT INTO reviewers (TGID, URL, Name, IsAdmin, Verified) VALUES (?, ?, ?, ?, 0)",
                (tg_id, url, name, 1 if is_adm else 0),
            )
            db.commit()
            print(f"\n{G}  ✅ Жюри {name} добавлен (не верифицирован).{RESET}")
        except sqlite3.IntegrityError:
            print(f"\n{R}  ❌ Такой tgID уже есть в БД.{RESET}")
    pause()


def set_verified(value: int):
    title = "Верифицировать жюри" if value else "Снять верификацию"
    tgid = _pick_reviewer(title)
    if not tgid:
        return
    with get_db() as db:
        db.execute("UPDATE reviewers SET Verified=? WHERE TGID=?", (value, tgid))
        db.commit()
    status = f"{G}верифицирован{RESET}" if value else f"{Y}верификация снята{RESET}"
    print(f"\n  ✅ Жюри tgID={tgid} {status}")
    pause()


def set_admin(value: int):
    title = "Назначить администратора" if value else "Снять права администратора"
    tgid = _pick_reviewer(title)
    if not tgid:
        return
    with get_db() as db:
        db.execute("UPDATE reviewers SET IsAdmin=? WHERE TGID=?", (value, tgid))
        db.commit()
    status = f"{G}назначен администратором{RESET}" if value else f"{Y}снят с администратора{RESET}"
    print(f"\n  ✅ Жюри tgID={tgid} {status}")
    pause()


def delete_reviewer():
    tgid = _pick_reviewer("❌ Удалить жюри")
    if not tgid:
        return
    with get_db() as db:
        row = db.execute("SELECT Name FROM reviewers WHERE TGID=?", (tgid,)).fetchone()
        name = row["Name"] if row else tgid
    if not confirm(f"Удалить жюри «{name}»? Его результаты сохранятся."):
        return
    with get_db() as db:
        db.execute("DELETE FROM reviewers WHERE TGID=?", (tgid,))
        db.commit()
    print(f"\n{G}  ✅ Жюри {name} удалён.{RESET}")
    pause()


# ══════════════════════════════════════════════════════════════════════════════
# ПОСТЫ
# ══════════════════════════════════════════════════════════════════════════════

def fix_pending_queue():
    """Добавляет pending-посты без записи в queue с учётом QUEUE_MODE."""
    from datetime import timezone
    from utils.config import QUEUE_MODE

    header("🔧 Восстановление очереди (fix_pending_queue)")

    with get_db() as db:
        orphans = db.execute(
            """
            SELECT p.ID, p.URL, a.Name AS author
            FROM posts_info p
            JOIN authors a ON p.Author = a.ID
            WHERE p.Status = 'pending'
              AND p.ID NOT IN (SELECT Post FROM queue)
            ORDER BY p.ID
            """
        ).fetchall()

    if not orphans:
        print(f"  {G}✅ Нет постов pending без очереди — всё в порядке.{RESET}")
        pause()
        return

    print(f"  {Y}Найдено постов без очереди: {len(orphans)}{RESET}")
    print(f"  Режим очереди: {BOLD}{QUEUE_MODE}{RESET}\n")

    # Предпросмотр
    for post in orphans:
        if QUEUE_MODE == "balanced" and _QM:
            with get_db() as db:
                rows = db.execute(
                    """
                    SELECT
                        rv.TGID,
                        COUNT(DISTINCT q.Post) AS in_queue,
                        COUNT(DISTINCT CASE WHEN r.HumanWords IS NOT NULL THEN r.Post END) AS checked,
                        COUNT(DISTINCT CASE WHEN p2.Status = 'rejected' AND r2.Reviewer = rv.TGID
                                            THEN r2.Post END) AS rejected
                    FROM reviewers rv
                    LEFT JOIN queue   q  ON q.Reviewer  = rv.TGID
                    LEFT JOIN results r  ON r.Reviewer  = rv.TGID
                    LEFT JOIN results r2 ON r2.Reviewer = rv.TGID
                    LEFT JOIN posts_info p2 ON p2.ID = r2.Post
                    WHERE rv.Verified = 1
                    GROUP BY rv.TGID
                    ORDER BY (in_queue + checked + rejected) ASC
                    """
                ).fetchall()
            import random
            if rows:
                min_total = rows[0]["in_queue"] + rows[0]["checked"] + rows[0]["rejected"]
                candidates = [
                    r["TGID"] for r in rows
                    if r["in_queue"] + r["checked"] + r["rejected"] == min_total
                ]
                reviewer = random.choice(candidates)
            else:
                reviewer = None
        else:
            reviewer = None
        label = reviewer or "общая очередь"
        print(f"  #{post['ID']} {post['author']:<20} → {label}")

    print()
    if not confirm(f"Добавить {len(orphans)} постов в очередь?"):
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    added = 0

    with get_db() as db:
        for post in orphans:
            if _QM:
                from parser.queue_manager import assign_post
                assign_post(post["ID"])
            else:
                db.execute(
                    "INSERT OR IGNORE INTO queue (Post, Reviewer, AssignedAt) VALUES (?, NULL, ?)",
                    (post["ID"], now),
                )
                db.commit()
            added += 1

    print(f"\n{G}  ✅ Добавлено в очередь: {added} постов.{RESET}")
    pause()


def manage_posts():
    actions = [
        view_all_posts, view_queue, find_post, find_post_by_author,
        release_stuck, reset_post, reject_post_cli, restore_post,
        reassign_queue, clear_queue, fix_pending_queue,
    ]
    while True:
        choice = menu("📝 Управление постами", [
            "Все посты",
            "Просмотр очереди",
            "Найти пост по URL",
            "Найти посты по автору",
            "Сбросить зависшие посты",
            "Сбросить проверку поста",
            "Отклонить пост вручную",
            "Восстановить отклонённый пост",
            "🔄 Переназначить очередь заново",
            "🗑  Очистить всю очередь",
            "🔧 Восстановить pending без очереди",
        ])
        if choice == -1:
            return
        actions[choice]()


def _status_label(status: str) -> str:
    return {
        "pending":       f"{Y}ожидает{RESET}",
        "checking":      f"{B}проверяется{RESET}",
        "done":          f"{G}проверен{RESET}",
        "rejected":      f"{R}отклонён{RESET}",
        "reviewer_post": f"{DIM}жюри-пост{RESET}",
    }.get(status, status)


def view_all_posts():
    header("📄 Все посты")
    status_opts = ["Все", "Ожидают", "Проверяются", "Проверены", "Отклонённые"]
    status_idx  = menu("Фильтр по статусу", status_opts)
    if status_idx == -1:
        return

    where = {
        0: "WHERE p.Status != 'reviewer_post'",
        1: "WHERE p.Status = 'pending'",
        2: "WHERE p.Status = 'checking'",
        3: "WHERE p.Status = 'done'",
        4: "WHERE p.Status = 'rejected'",
    }[status_idx]

    with get_db() as db:
        rows = db.execute(
            f"""
            SELECT p.ID, p.URL, p.Status, a.Name,
                   r.BotWords, r.HumanWords, r.HumanErrors
            FROM posts_info p
            JOIN authors    a ON p.Author = a.ID
            LEFT JOIN results r ON r.Post = p.ID
            {where}
            ORDER BY p.ID DESC
            LIMIT 50
            """
        ).fetchall()

    while True:
        header(f"📄 Посты — {status_opts[status_idx]} (последние {len(rows)})")
        print(f"  {'#':<6} {'Автор':<22} {'Статус':<14} {'Бот сл':>6} {'Чел сл':>7} {'Ош':>4}")
        hr()
        for r in rows:
            bot_w = str(r["BotWords"]    or "—")
            hum_w = str(r["HumanWords"]  or "—")
            hum_e = str(r["HumanErrors"] or "—")
            print(f"  {r['ID']:<6} {r['Name']:<22} {_status_label(r['Status']):<22} {bot_w:>6} {hum_w:>7} {hum_e:>4}")

        print()
        raw = input(f"  {W}ID поста для просмотра текста (0 — назад): {RESET}").strip()
        if not raw or raw == "0":
            return
        if not raw.isdigit():
            continue

        with get_db() as db:
            post = db.execute(
                """
                SELECT p.ID, p.URL, p.Text, p.Status, a.Name,
                       r.BotWords, r.HumanWords, r.HumanErrors, r.RejectReason
                FROM posts_info p
                JOIN authors    a ON p.Author = a.ID
                LEFT JOIN results r ON r.Post = p.ID
                WHERE p.ID = ?
                """,
                (int(raw),),
            ).fetchone()

        if not post:
            print(f"  {R}Пост не найден.{RESET}"); pause(); continue

        header(f"📄 Пост #{post['ID']}")
        print(f"  {BOLD}Автор:{RESET}  {post['Name']}")
        print(f"  {BOLD}URL:{RESET}    {DIM}{post['URL']}{RESET}")
        print(f"  {BOLD}Статус:{RESET} {_status_label(post['Status'])}", end="")
        if post["Status"] == "rejected":
            print(f"  |  Причина: {post['RejectReason'] or '—'}")
        elif post["Status"] == "done":
            print(f"  |  Слов: {post['HumanWords']}  Ошибок: {post['HumanErrors']}")
        else:
            print()
        print(f"  {BOLD}Бот слов:{RESET} {post['BotWords'] or '—'}")
        hr()
        text = post["Text"] or f"{DIM}(текст отсутствует){RESET}"
        for line in text.splitlines():
            print(f"  {line}")
        pause()


def view_queue():
    header("📋 Очереди жюри")

    if not _QM:
        print(f"  {R}queue_manager недоступен.{RESET}")
        pause()
        return

    sizes = get_all_reviewer_queue_sizes()
    if not sizes:
        print(f"  {R}Нет верифицированных жюри.{RESET}")
        pause()
        return

    total = 0
    for rv in sizes:
        count = rv["count"]
        total += count
        bar = f"{B}{'█' * min(count, 20)}{RESET}" if count else f"{DIM}пусто{RESET}"
        print(f"  {rv['name']:<22} {bar} {count}")

    hr()
    print(f"  Всего в очередях: {total}")

    # Свободные (Reviewer=NULL)
    with get_db() as db:
        free = db.execute(
            "SELECT COUNT(*) FROM queue q JOIN posts_info p ON q.Post=p.ID "
            "WHERE q.Reviewer IS NULL AND p.Status='pending'"
        ).fetchone()[0]
    if free:
        print(f"  {DIM}Свободных (без жюри): {free}{RESET}")

    pause()


def find_post():
    header("🔍 Найти пост по URL")
    url = input(f"  URL или часть: {W}").strip(); print(RESET, end="")
    with get_db() as db:
        rows = db.execute(
            """
            SELECT p.ID, p.URL, p.Status, a.Name, r.BotWords
            FROM posts_info p
            JOIN authors    a ON p.Author = a.ID
            LEFT JOIN results r ON r.Post = p.ID
            WHERE p.URL LIKE ?
            LIMIT 10
            """,
            (f"%{url}%",),
        ).fetchall()

    if not rows:
        print(f"  {R}Не найдено.{RESET}")
    else:
        for r in rows:
            print(f"\n  {BOLD}#{r['ID']}{RESET} {r['Name']}")
            print(f"  {DIM}{r['URL']}{RESET}")
            print(f"  Статус: {_status_label(r['Status'])}  |  Бот: {r['BotWords'] or '—'} слов")
    pause()


def find_post_by_author():
    header("🔍 Найти посты по автору")
    name = input(f"  Имя автора или часть: {W}").strip(); print(RESET, end="")
    if not name:
        return
    with get_db() as db:
        rows = db.execute(
            """
            SELECT p.ID, p.URL, p.Status, a.Name,
                   r.BotWords, r.HumanWords, r.HumanErrors
            FROM posts_info p
            JOIN authors    a ON p.Author = a.ID
            LEFT JOIN results r ON r.Post = p.ID
            WHERE a.Name LIKE ?
            ORDER BY p.ID DESC
            LIMIT 20
            """,
            (f"%{name}%",),
        ).fetchall()

    if not rows:
        print(f"  {R}Не найдено.{RESET}"); pause(); return

    header(f"🔍 Посты «{name}» ({len(rows)} шт.)")
    print(f"  {'#':<6} {'Автор':<22} {'Статус':<14} {'Бот сл':>6} {'Чел сл':>7} {'Ош':>4}")
    hr()
    for r in rows:
        print(
            f"  {r['ID']:<6} {r['Name']:<22} {_status_label(r['Status']):<22} "
            f"{str(r['BotWords'] or '—'):>6} {str(r['HumanWords'] or '—'):>7} {str(r['HumanErrors'] or '—'):>4}"
        )
    pause()


def release_stuck():
    """Сбрасывает посты со статусом 'checking', у которых жюри не закончил проверку."""
    header("🔓 Сброс зависших постов")
    with get_db() as db:
        rows = db.execute(
            """
            SELECT q.Post, p.URL, a.Name AS author,
                   rv.Name AS reviewer, q.TakenAt
            FROM queue q
            JOIN posts_info p  ON q.Post     = p.ID
            JOIN authors    a  ON p.Author   = a.ID
            LEFT JOIN reviewers rv ON rv.TGID = q.Reviewer
            WHERE q.TakenAt IS NOT NULL
              AND p.Status = 'checking'
            """
        ).fetchall()

    if not rows:
        print(f"  {G}Зависших постов нет!{RESET}"); pause(); return

    print(f"  {Y}Найдено: {len(rows)}{RESET}\n")
    print(f"  {'#':<6} {'Автор поста':<22} {'Взял жюри':<20}  Взято в")
    hr()
    for r in rows:
        reviewer  = r["reviewer"] or "—"
        url_short = (r["URL"] or "")[-45:]
        print(f"  {r['Post']:<6} {r['author']:<22} {reviewer:<20}  {r['TakenAt']}")

    print()
    if not confirm(f"Освободить все {len(rows)} зависших постов?"):
        return

    with get_db() as db:
        for r in rows:
            db.execute(
                "UPDATE queue SET Reviewer=NULL, TakenAt=NULL WHERE Post=?",
                (r["Post"],),
            )
            db.execute(
                "UPDATE posts_info SET Status='pending' WHERE ID=?",
                (r["Post"],),
            )
        db.commit()

    print(f"\n{G}  ✅ Освобождено {len(rows)} постов.{RESET}")
    pause()


def reset_post():
    header("🔄 Сбросить проверку поста")
    raw = input(f"  ID поста: {W}").strip(); print(RESET, end="")
    if not raw.isdigit():
        print(f"{R}  Нужен числовой ID.{RESET}"); pause(); return

    pid = int(raw)
    with get_db() as db:
        row = db.execute("SELECT URL, Status FROM posts_info WHERE ID=?", (pid,)).fetchone()
        if not row:
            print(f"{R}  Пост #{pid} не найден.{RESET}"); pause(); return
        if not confirm(f"Сбросить проверку поста #{pid} (статус: {row['Status']})?"):
            return
        # Возвращаем в очередь
        db.execute(
            "UPDATE posts_info SET Status='pending' WHERE ID=?", (pid,)
        )
        db.execute(
            "UPDATE queue SET Reviewer=NULL, TakenAt=NULL WHERE Post=?", (pid,)
        )
        db.execute(
            "UPDATE results SET HumanWords=NULL, HumanErrors=NULL, Reviewer=NULL, "
            "RejectReason=NULL WHERE Post=?",
            (pid,),
        )
        db.commit()

    # Если поста не было в очереди — добавляем
    if _QM:
        with get_db() as db:
            in_queue = db.execute("SELECT 1 FROM queue WHERE Post=?", (pid,)).fetchone()
        if not in_queue:
            assign_post(pid)

    print(f"\n{G}  ✅ Пост #{pid} сброшен и возвращён в очередь.{RESET}")
    pause()


def reject_post_cli():
    header("❌ Отклонить пост")
    raw = input(f"  ID поста: {W}").strip(); print(RESET, end="")
    if not raw.isdigit():
        print(f"{R}  Нужен числовой ID.{RESET}"); pause(); return

    reason = input(f"  Причина: {W}").strip(); print(RESET, end="")
    pid    = int(raw)

    with get_db() as db:
        row = db.execute("SELECT URL FROM posts_info WHERE ID=?", (pid,)).fetchone()
        if not row:
            print(f"{R}  Пост #{pid} не найден.{RESET}"); pause(); return
        if not confirm(f"Отклонить пост #{pid}?"):
            return
        db.execute(
            "UPDATE posts_info SET Status='rejected' WHERE ID=?", (pid,)
        )
        exists = db.execute("SELECT ID FROM results WHERE Post=?", (pid,)).fetchone()
        if exists:
            db.execute(
                "UPDATE results SET Reviewer=NULL, RejectReason=? WHERE Post=?",
                (reason, pid),
            )
        else:
            db.execute(
                "INSERT INTO results (Post, RejectReason) VALUES (?, ?)", (pid, reason)
            )
        db.commit()

    if _QM:
        remove_post(pid)

    print(f"\n{G}  ✅ Пост #{pid} отклонён.{RESET}")
    pause()


def restore_post():
    header("♻️  Восстановить отклонённый пост")
    with get_db() as db:
        rows = db.execute(
            """
            SELECT p.ID, p.URL, a.Name, r.RejectReason
            FROM posts_info p
            JOIN authors    a ON p.Author = a.ID
            LEFT JOIN results r ON r.Post = p.ID
            WHERE p.Status = 'rejected'
            ORDER BY p.ID DESC
            LIMIT 20
            """
        ).fetchall()

    if not rows:
        print(f"  {G}Нет отклонённых постов.{RESET}"); pause(); return

    opts = [f"#{r['ID']} {r['Name']} — {(r['RejectReason'] or '')[:30]}" for r in rows]
    idx  = menu("Выберите пост для восстановления", opts)
    if idx == -1:
        return

    pid = rows[idx]["ID"]
    with get_db() as db:
        db.execute(
            "UPDATE posts_info SET Status='pending' WHERE ID=?", (pid,)
        )
        db.execute(
            "UPDATE results SET HumanWords=NULL, HumanErrors=NULL, Reviewer=NULL, "
            "RejectReason=NULL WHERE Post=?",
            (pid,),
        )
        db.commit()

    if _QM:
        with get_db() as db:
            in_queue = db.execute("SELECT 1 FROM queue WHERE Post=?", (pid,)).fetchone()
        if not in_queue:
            assign_post(pid)

    print(f"\n{G}  ✅ Пост #{pid} восстановлен и возвращён в очередь.{RESET}")
    pause()


def clear_queue():
    header("🗑  Очистить все очереди")
    total = get_total_queue_count() if _QM else 0

    if not confirm(f"Удалить ВСЕ {total} постов из очереди? (посты останутся в posts_info со статусом pending)"):
        return

    with get_db() as db:
        db.execute("UPDATE posts_info SET Status='pending' WHERE Status IN ('checking', 'pending')")
        db.execute("DELETE FROM queue")
        db.commit()

    print(f"\n{G}  ✅ Все очереди очищены.{RESET}")
    pause()


def reassign_queue():
    """Очищает очередь и переназначает все pending/checking посты заново."""
    header("🔄 Переназначить очередь")

    with get_db() as db:
        post_count = db.execute(
            "SELECT COUNT(*) FROM posts_info WHERE Status IN ('pending', 'checking')"
        ).fetchone()[0]

    print(f"  Непроверенных постов: {BOLD}{post_count}{RESET}")
    print()
    print(f"  {Y}Что произойдёт:{RESET}")
    print(f"  1. Текущая очередь будет очищена")
    print(f"  2. Все {post_count} постов будут распределены заново по текущему QUEUE_MODE")
    print()

    if not confirm(f"Переназначить все {post_count} постов?"):
        return

    with get_db() as db:
        db.execute("UPDATE posts_info SET Status='pending' WHERE Status='checking'")
        db.execute("DELETE FROM queue")
        db.commit()
        posts = db.execute(
            "SELECT ID FROM posts_info WHERE Status='pending' ORDER BY ID"
        ).fetchall()

    if not _QM:
        print(f"  {R}queue_manager недоступен.{RESET}")
        pause()
        return

    ok = 0
    for p in posts:
        assign_post(p["ID"])
        ok += 1

    print(f"\n{G}  ✅ Переназначено: {ok} из {len(posts)} постов.{RESET}")
    pause()


# ══════════════════════════════════════════════════════════════════════════════
# ДНИ
# ══════════════════════════════════════════════════════════════════════════════

def manage_days():
    actions = [list_days, create_day, finish_day, delete_day]
    while True:
        choice = menu("📅 Управление днями", [
            "Список дней",
            "Создать новый день",
            "Завершить текущий день",
            "Удалить день",
        ])
        if choice == -1:
            return
        actions[choice]()


def list_days():
    header("📅 Дни конкурса")
    with get_db() as db:
        rows    = db.execute("SELECT Day, Data FROM days ORDER BY Day DESC").fetchall()
        current = db.execute("SELECT MAX(Day) FROM days").fetchone()[0]

    if not rows:
        print(f"  {DIM}Дней нет.{RESET}")
    else:
        for r in rows:
            marker = f" {C}← текущий{RESET}" if r["Day"] == current else ""
            print(f"  День {r['Day']}: {r['Data']}{marker}")
    pause()


def create_day():
    header("➕ Создать новый день")
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y")
    label = input(f"  Название/дата [{today}]: {W}").strip(); print(RESET, end="")
    if not label:
        label = today
    if not confirm(f"Создать день «{label}»?"):
        return
    with get_db() as db:
        db.execute("INSERT INTO days (Data) VALUES (?)", (label,))
        db.commit()
    print(f"\n{G}  ✅ День «{label}» создан.{RESET}")
    pause()


def finish_day():
    header("🏁 Завершить текущий день")
    with get_db() as db:
        row = db.execute("SELECT Day, Data FROM days ORDER BY Day DESC LIMIT 1").fetchone()
    if not row:
        print(f"  {R}Нет активного дня.{RESET}"); pause(); return

    print(f"  Текущий день: {BOLD}{row['Data']}{RESET}")
    if not confirm("Завершить и начать новый?"):
        return

    today = datetime.now().strftime("%d.%m.%Y")
    with get_db() as db:
        db.execute("INSERT INTO days (Data) VALUES (?)", (today,))
        db.commit()
    print(f"\n{G}  ✅ День {row['Data']} завершён. Начат новый: {today}{RESET}")
    pause()


def delete_day():
    header("🗑  Удалить день")
    with get_db() as db:
        rows = db.execute("SELECT Day, Data FROM days ORDER BY Day DESC").fetchall()
    if not rows:
        print(f"  {R}Дней нет.{RESET}"); pause(); return

    opts = [f"День {r['Day']}: {r['Data']}" for r in rows]
    idx  = menu("Выберите день для удаления", opts)
    if idx == -1:
        return

    day_id   = rows[idx]["Day"]
    day_data = rows[idx]["Data"]

    with get_db() as db:
        post_count = db.execute(
            "SELECT COUNT(*) FROM posts_info WHERE Day = ?", (day_id,)
        ).fetchone()[0]

    target_day = None
    if post_count > 0:
        print(f"\n  {Y}⚠  К этому дню привязано {post_count} постов.{RESET}")
        other_rows = [r for r in rows if r["Day"] != day_id]
        if not other_rows:
            print(f"  {R}Нет других дней для переноса постов.{RESET}")
            pause()
            return
        other_opts = [f"День {r['Day']}: {r['Data']}" for r in other_rows]
        print()
        target_idx = menu("Куда перенести посты?", other_opts)
        if target_idx == -1:
            return
        target_day      = other_rows[target_idx]["Day"]
        target_day_data = other_rows[target_idx]["Data"]
        print(f"\n  Посты будут перенесены в День {target_day} ({target_day_data})")

    if not confirm(f"Удалить день {day_id} ({day_data})?"):
        return

    with get_db() as db:
        if target_day is not None:
            db.execute(
                "UPDATE posts_info SET Day = ? WHERE Day = ?",
                (target_day, day_id),
            )
            print(f"  {G}✅ {post_count} постов перенесено в День {target_day}.{RESET}")
        db.execute("DELETE FROM days WHERE Day = ?", (day_id,))
        db.commit()

    print(f"\n{G}  ✅ День {day_id} ({day_data}) удалён.{RESET}")
    pause()


# ══════════════════════════════════════════════════════════════════════════════
# ССЫЛКИ
# ══════════════════════════════════════════════════════════════════════════════

def manage_links():
    actions = [
        links_stats, view_links, delete_link, add_link,
        add_to_blacklist, view_blacklist, remove_from_blacklist,
    ]
    while True:
        choice = menu("🔗 Управление ссылками", [
            "Статистика ссылок",
            "Просмотр ссылок",
            "Удалить ссылку",
            "Добавить ссылку вручную",
            "Добавить в блэклист",
            "Просмотр блэклиста",
            "Удалить из блэклиста",
        ])
        if choice == -1:
            return
        actions[choice]()


def links_stats():
    header("🔗 Статистика ссылок")
    with get_db() as db:
        total    = db.execute("SELECT COUNT(*) FROM links").fetchone()[0]
        parsed   = db.execute("SELECT COUNT(*) FROM links WHERE Parsed=1").fetchone()[0]
        unparsed = total - parsed
        bl_count = db.execute("SELECT COUNT(*) FROM blacklist").fetchone()[0]

    print(f"  Всего ссылок:       {total}")
    print(f"  Распарсено:         {G}{parsed}{RESET}")
    print(f"  Ожидают парсинга:   {Y}{unparsed}{RESET}")
    print(f"  В блэклисте:        {R}{bl_count}{RESET}")
    pause()


def view_links():
    header("🔗 Ссылки (последние 30)")
    with get_db() as db:
        rows = db.execute(
            "SELECT URL, COALESCE(Parsed, 0) as Parsed FROM links ORDER BY rowid DESC LIMIT 30"
        ).fetchall()
    if not rows:
        print(f"  {DIM}Ссылок нет.{RESET}")
    else:
        for r in rows:
            status    = f"{G}✅{RESET}" if r["Parsed"] else f"{Y}⏳{RESET}"
            url_short = (r["URL"] or "")[-80:]
            print(f"  {status} {DIM}{url_short}{RESET}")
    pause()


def delete_link():
    header("🗑  Удалить ссылку")
    url = input(f"  URL или часть: {W}").strip(); print(RESET, end="")
    if not url:
        return
    with get_db() as db:
        rows = db.execute(
            "SELECT URL FROM links WHERE URL LIKE ? LIMIT 10", (f"%{url}%",)
        ).fetchall()
    if not rows:
        print(f"  {R}Не найдено.{RESET}"); pause(); return

    opts = [(r["URL"] or "")[-80:] for r in rows]
    idx  = menu("Выберите ссылку для удаления", opts)
    if idx == -1:
        return

    target = rows[idx]["URL"]
    if not confirm(f"Удалить ссылку?\n  {target}"):
        return
    with get_db() as db:
        db.execute("DELETE FROM links WHERE URL=?", (target,))
        db.commit()
    print(f"\n{G}  ✅ Ссылка удалена.{RESET}")
    pause()


def add_link():
    header("➕ Добавить ссылку")
    url = input(f"  URL: {W}").strip(); print(RESET, end="")
    if not url:
        return
    with get_db() as db:
        db.execute("INSERT OR IGNORE INTO links (URL, Parsed) VALUES (?, 0)", (url,))
        db.commit()
    print(f"\n{G}  ✅ Ссылка добавлена.{RESET}")
    pause()


def add_to_blacklist():
    header("🚫 Добавить в блэклист")
    url = input(f"  URL: {W}").strip(); print(RESET, end="")
    if not url:
        return
    with get_db() as db:
        db.execute("INSERT OR IGNORE INTO blacklist (URL) VALUES (?)", (url,))
        db.execute("DELETE FROM links WHERE URL=?", (url,))
        db.commit()
    print(f"\n{G}  ✅ Добавлено в блэклист.{RESET}")
    pause()


def view_blacklist():
    header("🚫 Блэклист")
    with get_db() as db:
        rows = db.execute(
            "SELECT rowid, URL FROM blacklist ORDER BY rowid DESC LIMIT 30"
        ).fetchall()
    if not rows:
        print(f"  {DIM}Блэклист пуст.{RESET}")
    else:
        for r in rows:
            print(f"  {DIM}{r['URL']}{RESET}")
    pause()


def remove_from_blacklist():
    header("♻️  Удалить из блэклиста")
    with get_db() as db:
        rows = db.execute(
            "SELECT rowid, URL FROM blacklist ORDER BY rowid DESC LIMIT 20"
        ).fetchall()
    if not rows:
        print(f"  {DIM}Блэклист пуст.{RESET}"); pause(); return

    opts = [(r["URL"] or "")[-70:] for r in rows]
    idx  = menu("Выберите URL для удаления", opts)
    if idx == -1:
        return

    with get_db() as db:
        db.execute("DELETE FROM blacklist WHERE rowid=?", (rows[idx]["rowid"],))
        db.commit()
    print(f"\n{G}  ✅ Удалено из блэклиста.{RESET}")
    pause()


# ══════════════════════════════════════════════════════════════════════════════
# ЛОГИ
# ══════════════════════════════════════════════════════════════════════════════

def view_logs():
    header("📄 Просмотр логов")
    logs_dir = ROOT / "logs"
    if not logs_dir.exists():
        print(f"  {R}Папка логов не найдена.{RESET}"); pause(); return

    log_files = sorted(logs_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not log_files:
        print(f"  {R}Лог файлы не найдены.{RESET}"); pause(); return

    log_file = log_files[0]
    counts = [50, 100, 200]
    idx    = menu(f"Файл: {log_file.name}\nСколько строк показать?", ["50 строк", "100 строк", "200 строк"])
    if idx == -1:
        return

    n = counts[idx]
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        last = lines[-n:] if len(lines) >= n else lines
    except Exception as e:
        print(f"  {R}Ошибка чтения: {e}{RESET}"); pause(); return

    header(f"📄 Последние {n} строк — {log_file.name}")
    for line in last:
        line = line.rstrip()
        if "ERROR" in line or "Ошибка" in line:
            print(f"  {R}{line}{RESET}")
        elif "WARNING" in line:
            print(f"  {Y}{line}{RESET}")
        elif "INFO" in line:
            print(f"  {DIM}{line}{RESET}")
        else:
            print(f"  {line}")
    pause()


# ══════════════════════════════════════════════════════════════════════════════
# ПОЛНАЯ ОЧИСТКА
# ══════════════════════════════════════════════════════════════════════════════

def full_reset():
    header("🗑  Полная очистка данных")

    TABLES = [
        ("queue",      "Очередь"),
        ("results",    "Результаты"),
        ("posts_info", "Посты"),
        ("links",      "Ссылки"),
        ("authors",    "Авторы"),
    ]

    with get_db() as db:
        print(f"  {'Таблица':<15} {'Записей':>8}")
        hr()
        counts = {}
        for table, label in TABLES:
            count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            counts[table] = count
            print(f"  {label:<15} {R}{count:>8}{RESET}")

    print()
    print(f"  {R}{BOLD}⚠  Это удалит ВСЕ данные из этих таблиц!{RESET}")
    print(f"  {DIM}Таблицы reviewers, days и blacklist не затрагиваются.{RESET}")
    print()

    if not confirm("Ты уверен? Это действие необратимо"):
        print(f"\n  {DIM}Отменено.{RESET}"); pause(); return
    if not confirm("Подтверди ещё раз — удалить все данные"):
        print(f"\n  {DIM}Отменено.{RESET}"); pause(); return

    with get_db() as db:
        for table, label in TABLES:
            db.execute(f"DELETE FROM {table}")
            print(f"  {G}✅ {label} очищена{RESET}  ({counts[table]} записей)")
        db.execute(
            "DELETE FROM sqlite_sequence WHERE name IN ('results','posts_info','authors')"
        )
        db.commit()

    print(f"\n{G}  ✅ Очистка завершена.{RESET}")
    pause()


# ══════════════════════════════════════════════════════════════════════════════
# ИНИЦИАЛИЗАЦИЯ БД
# ══════════════════════════════════════════════════════════════════════════════

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS reviewers (
    TGID     TEXT    PRIMARY KEY,
    URL      TEXT    NOT NULL UNIQUE,
    Name     TEXT    NOT NULL,
    IsAdmin  INTEGER NOT NULL DEFAULT 0,
    Verified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS authors (
    ID   INTEGER PRIMARY KEY AUTOINCREMENT,
    Name TEXT    NOT NULL,
    URL  TEXT    NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS days (
    Day  INTEGER PRIMARY KEY AUTOINCREMENT,
    Data TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS links (
    URL    TEXT    PRIMARY KEY,
    Parsed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS blacklist (
    URL TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS posts_info (
    ID     INTEGER PRIMARY KEY AUTOINCREMENT,
    Author INTEGER NOT NULL REFERENCES authors(ID),
    URL    TEXT    NOT NULL UNIQUE,
    Text   TEXT,
    Day    INTEGER REFERENCES days(Day),
    Status TEXT    NOT NULL DEFAULT 'pending'
        CHECK(Status IN ('pending','checking','done','rejected','reviewer_post'))
);

CREATE TABLE IF NOT EXISTS queue (
    Post       INTEGER NOT NULL PRIMARY KEY REFERENCES posts_info(ID),
    Reviewer   TEXT    REFERENCES reviewers(TGID),
    AssignedAt TEXT    NOT NULL DEFAULT (datetime('now','utc')),
    TakenAt    TEXT
);

CREATE TABLE IF NOT EXISTS results (
    ID            INTEGER PRIMARY KEY AUTOINCREMENT,
    Post          INTEGER NOT NULL UNIQUE REFERENCES posts_info(ID),
    BotWords      INTEGER,
    HumanWords    INTEGER,
    HumanErrors   INTEGER,
    ErrorsPer1000 REAL GENERATED ALWAYS AS (
        ROUND(HumanErrors * 1000.0 / NULLIF(HumanWords, 0), 2)
    ) STORED,
    RejectReason  TEXT,
    Reviewer      TEXT REFERENCES reviewers(TGID)
);

CREATE INDEX IF NOT EXISTS idx_links_parsed       ON links(Parsed);
CREATE INDEX IF NOT EXISTS idx_posts_status       ON posts_info(Status);
CREATE INDEX IF NOT EXISTS idx_queue_reviewer     ON queue(Reviewer);
CREATE INDEX IF NOT EXISTS idx_queue_assignedat   ON queue(AssignedAt);
CREATE INDEX IF NOT EXISTS idx_queue_takenat      ON queue(TakenAt);
CREATE INDEX IF NOT EXISTS idx_results_reviewer   ON results(Reviewer);
"""

DB_PATH = ROOT / "data" / "main.db"


def init_db():
    header("🗄  Инициализация базы данных")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not DB_PATH.exists()

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()

    status = f"{G}создана{RESET}" if is_new else f"{Y}уже существует, проверена{RESET}"
    print(f"  ✅ База данных {status}: {DB_PATH}\n")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    print(f"  {'Таблица':<22} {'Записей':>8}")
    hr()
    for t in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {t['name']}").fetchone()[0]
        print(f"  {t['name']:<22} {count:>8}")
    conn.close()
    pause()


# ══════════════════════════════════════════════════════════════════════════════
# ЭКСПОРТ РЕЗУЛЬТАТОВ
# ══════════════════════════════════════════════════════════════════════════════

def export_results():
    header("📊 Экспорт результатов в Excel")
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        print(f"  {R}❌ Не установлен openpyxl. Выполни: pip install openpyxl{RESET}")
        pause()
        return

    # ── Стили ─────────────────────────────────────────────────────────────────
    CLR_HEADER    = "2C3E50"
    CLR_SUBHEADER = "5D6D7E"
    CLR_ACCENT    = "3498DB"
    CLR_EVEN      = "EBF5FB"
    CLR_WHITE     = "FFFFFF"
    CLR_LIGHT     = "FFFFFF"
    CLR_DARK      = "1A1A1A"

    def _font(size=10, bold=False, color=CLR_DARK):
        return Font(name="Arial", size=size, bold=bold, color=color)

    def _fill(hex_color):
        return PatternFill("solid", fgColor=hex_color)

    def _border():
        s = Side(style="thin", color="BDC3C7")
        return Border(left=s, right=s, top=s, bottom=s)

    def _align(h="center"):
        return Alignment(horizontal=h, vertical="center", wrap_text=True)

    def _header_row(ws, row, cols, bg=CLR_HEADER):
        for c in range(1, cols + 1):
            cell = ws.cell(row=row, column=c)
            cell.font      = _font(10, bold=True, color=CLR_LIGHT)
            cell.fill      = _fill(bg)
            cell.alignment = _align("center")
            cell.border    = _border()
        ws.row_dimensions[row].height = 22

    def _data_row(ws, row, cols, even=False):
        for c in range(1, cols + 1):
            cell = ws.cell(row=row, column=c)
            cell.font      = _font(10)
            cell.fill      = _fill(CLR_EVEN if even else CLR_WHITE)
            cell.alignment = _align("left" if c == 1 else "center")
            cell.border    = _border()
        ws.row_dimensions[row].height = 20

    def _totals_row(ws, row, cols):
        for c in range(1, cols + 1):
            cell = ws.cell(row=row, column=c)
            cell.font      = _font(10, bold=True, color=CLR_LIGHT)
            cell.fill      = _fill(CLR_HEADER)
            cell.alignment = _align("center")
            cell.border    = _border()
        ws.row_dimensions[row].height = 22

    # ── Запросы ────────────────────────────────────────────────────────────────
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    days = conn.execute("SELECT Day, Data FROM days ORDER BY Day").fetchall()

    def get_posts_by_day(day_id):
        return conn.execute(
            """
            SELECT a.Name AS author, COUNT(p.ID) AS post_count,
                   COALESCE(SUM(r.HumanWords), 0) AS words,
                   COALESCE(SUM(r.HumanErrors), 0) AS errors
            FROM posts_info p
            JOIN authors a ON p.Author = a.ID
            JOIN results r ON r.Post = p.ID
            WHERE p.Day = ? AND p.Status = 'done' AND r.HumanWords IS NOT NULL
            GROUP BY a.ID
            ORDER BY errors * 1.0 / NULLIF(words, 0) ASC
            """, (day_id,)
        ).fetchall()

    def get_summary_by_day():
        return conn.execute(
            """
            SELECT d.Data AS date, COUNT(p.ID) AS posts,
                   COALESCE(SUM(r.HumanWords), 0) AS words,
                   COALESCE(SUM(r.HumanErrors), 0) AS errors
            FROM days d
            LEFT JOIN posts_info p ON p.Day = d.Day AND p.Status = 'done'
            LEFT JOIN results r ON r.Post = p.ID AND r.HumanWords IS NOT NULL
            GROUP BY d.Day ORDER BY d.Day
            """
        ).fetchall()

    def get_top_authors():
        return conn.execute(
            """
            SELECT a.Name AS author, COUNT(p.ID) AS post_count,
                   COALESCE(SUM(r.HumanWords), 0) AS words,
                   COALESCE(SUM(r.HumanErrors), 0) AS errors
            FROM posts_info p
            JOIN authors a ON p.Author = a.ID
            JOIN results r ON r.Post = p.ID
            WHERE p.Status = 'done' AND r.HumanWords IS NOT NULL
            GROUP BY a.ID HAVING words > 0
            ORDER BY errors * 1.0 / words ASC
            """
        ).fetchall()

    def get_reviewer_stats():
        return conn.execute(
            """
            SELECT rv.Name, COUNT(r.ID) AS checked,
                   COALESCE(SUM(r.HumanWords), 0) AS words,
                   COALESCE(SUM(r.HumanErrors), 0) AS errors
            FROM reviewers rv
            LEFT JOIN results r ON r.Reviewer = rv.TGID AND r.HumanWords IS NOT NULL
            WHERE rv.Verified = 1
            GROUP BY rv.TGID ORDER BY checked DESC
            """
        ).fetchall()

    # ── Сборка Excel ──────────────────────────────────────────────────────────
    wb = Workbook()
    wb.remove(wb.active)

    # Лист: Общий отчёт
    ws = wb.create_sheet("Общий отчёт")
    ws.sheet_view.showGridLines = False

    ws.merge_cells("A1:F1")
    ws["A1"].value     = "ИТОГИ КОНКУРСА inkstory.net"
    ws["A1"].font      = _font(16, bold=True, color=CLR_LIGHT)
    ws["A1"].fill      = _fill(CLR_HEADER)
    ws["A1"].alignment = _align("center")
    ws.row_dimensions[1].height = 36

    ws.merge_cells("A2:F2")
    ws["A2"].value     = f"Сформировано: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    ws["A2"].font      = _font(10, color=CLR_LIGHT)
    ws["A2"].fill      = _fill(CLR_SUBHEADER)
    ws["A2"].alignment = _align("center")
    ws.row_dimensions[2].height = 18

    ws.row_dimensions[3].height = 10

    ws.merge_cells("A4:F4")
    ws["A4"].value     = "ПО ДНЯМ КОНКУРСА"
    ws["A4"].font      = _font(11, bold=True, color=CLR_LIGHT)
    ws["A4"].fill      = _fill(CLR_ACCENT)
    ws["A4"].alignment = _align("center")
    ws.row_dimensions[4].height = 24

    for c, h in enumerate(["Дата", "Принято постов", "Слов (жюри)", "Ошибок", "Ошибок / 1000 слов", ""], 1):
        ws.cell(row=5, column=c).value = h
    _header_row(ws, 5, 5)

    day_summary = get_summary_by_day()
    for i, d in enumerate(day_summary, 1):
        r = 5 + i
        for c, v in enumerate([d["date"], d["posts"], d["words"], d["errors"], f"=IFERROR(ROUND(D{r}/C{r}*1000,1),0)"], 1):
            ws.cell(row=r, column=c).value = v
        _data_row(ws, r, 5, even=(i % 2 == 0))

    first, last = 6, 5 + len(day_summary)
    tr = last + 1
    _totals_row(ws, tr, 5)
    ws.cell(row=tr, column=1).value = "ИТОГО"
    for c, v in enumerate([f"=SUM(B{first}:B{last})", f"=SUM(C{first}:C{last})", f"=SUM(D{first}:D{last})", f"=IFERROR(ROUND(D{tr}/C{tr}*1000,1),0)"], 2):
        ws.cell(row=tr, column=c).value = v

    r2 = tr + 2
    ws.merge_cells(f"A{r2}:F{r2}")
    ws[f"A{r2}"].value = "СТАТИСТИКА ЖЮРИ"
    ws[f"A{r2}"].font = _font(11, bold=True, color=CLR_LIGHT)
    ws[f"A{r2}"].fill = _fill(CLR_ACCENT)
    ws[f"A{r2}"].alignment = _align("center")
    ws.row_dimensions[r2].height = 24

    r2 += 1
    for c, h in enumerate(["Жюри", "Проверено постов", "Слов проверено", "Ошибок найдено", "Ошибок / 1000 слов", ""], 1):
        ws.cell(row=r2, column=c).value = h
    _header_row(ws, r2, 5)

    for i, rv in enumerate(get_reviewer_stats(), 1):
        r2 += 1
        for c, v in enumerate([rv["Name"], rv["checked"], rv["words"], rv["errors"], f"=IFERROR(ROUND(D{r2}/C{r2}*1000,1),0)"], 1):
            ws.cell(row=r2, column=c).value = v
        _data_row(ws, r2, 5, even=(i % 2 == 0))

    r3 = r2 + 2
    ws.merge_cells(f"A{r3}:F{r3}")
    ws[f"A{r3}"].value = "ТОП УЧАСТНИКОВ — меньше всего ошибок на 1000 слов"
    ws[f"A{r3}"].font = _font(11, bold=True, color=CLR_LIGHT)
    ws[f"A{r3}"].fill = _fill(CLR_ACCENT)
    ws[f"A{r3}"].alignment = _align("center")
    ws.row_dimensions[r3].height = 24

    r3 += 1
    for c, h in enumerate(["#", "Участник", "Постов", "Слов", "Ошибок", "Ошибок / 1000 слов"], 1):
        ws.cell(row=r3, column=c).value = h
    _header_row(ws, r3, 6)

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for i, a in enumerate(get_top_authors(), 1):
        r3 += 1
        for c, v in enumerate([medals.get(i, i), a["author"], a["post_count"], a["words"], a["errors"], f"=IFERROR(ROUND(E{r3}/D{r3}*1000,1),0)"], 1):
            ws.cell(row=r3, column=c).value = v
        _data_row(ws, r3, 6, even=(i % 2 == 0))

    for col, w in zip("ABCDEF", [6, 24, 10, 12, 12, 22]):
        ws.column_dimensions[col].width = w

    # Листы по дням
    for day in days:
        ws_day = wb.create_sheet(day["Data"])
        ws_day.sheet_view.showGridLines = False

        ws_day.merge_cells("A1:F1")
        ws_day["A1"].value     = day["Data"]
        ws_day["A1"].font      = _font(14, bold=True, color=CLR_LIGHT)
        ws_day["A1"].fill      = _fill(CLR_HEADER)
        ws_day["A1"].alignment = _align("center")
        ws_day.row_dimensions[1].height = 30
        ws_day.row_dimensions[2].height = 8

        for c, h in enumerate(["Юзер", "Постов", "Слов", "Ошибок", "Ошибок / 1000 слов", ""], 1):
            ws_day.cell(row=3, column=c).value = h
        _header_row(ws_day, 3, 5)

        posts = get_posts_by_day(day["Day"])
        if not posts:
            ws_day.merge_cells("A4:F4")
            ws_day["A4"].value     = "Нет данных за этот день"
            ws_day["A4"].font      = _font(color="888888")
            ws_day["A4"].alignment = _align("center")
        else:
            for i, p in enumerate(posts, 1):
                r = 3 + i
                for c, v in enumerate([p["author"], p["post_count"], p["words"], p["errors"], f"=IFERROR(ROUND(D{r}/C{r}*1000,1),0)"], 1):
                    ws_day.cell(row=r, column=c).value = v
                _data_row(ws_day, r, 5, even=(i % 2 == 0))

            first, last = 4, 3 + len(posts)
            tr = last + 1
            _totals_row(ws_day, tr, 5)
            ws_day.cell(row=tr, column=1).value = "ИТОГО"
            for c, v in enumerate([f"=SUM(B{first}:B{last})", f"=SUM(C{first}:C{last})", f"=SUM(D{first}:D{last})", f"=IFERROR(ROUND(D{tr}/C{tr}*1000,1),0)"], 2):
                ws_day.cell(row=tr, column=c).value = v

        for col, w in zip("ABCDEF", [22, 10, 12, 12, 22, 4]):
            ws_day.column_dimensions[col].width = w

    conn.close()

    results_dir = ROOT / "results"
    results_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out_path  = results_dir / f"results_{timestamp}.xlsx"
    wb.save(out_path)

    print(f"  {G}✅ Экспорт завершён:{RESET} {out_path}")
    pause()


# ══════════════════════════════════════════════════════════════════════════════
# ГЛАВНОЕ МЕНЮ
# ══════════════════════════════════════════════════════════════════════════════

def main():
    actions = [
        show_stats, manage_reviewers, manage_posts,
        manage_days, manage_links, view_logs,
        init_db, export_results, full_reset,
    ]
    try:
        while True:
            choice = menu("Главное меню", [
                "📊 Статистика",
                "👥 Управление жюри",
                "📝 Управление постами",
                "📅 Управление днями",
                "🔗 Управление ссылками",
                "📄 Просмотр логов",
                "🗄  Инициализация БД",
                "📤 Экспорт результатов",
                "🗑  Полная очистка данных",
            ], show_stats=True)
            if choice == -1:
                break
            actions[choice]()
    except KeyboardInterrupt:
        pass
    finally:
        clr()
        print(f"{DIM}Выход.{RESET}\n")


if __name__ == "__main__":
    main()
