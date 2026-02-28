"""
cli.py — консольное управление БД inkstory

Запуск: python cli.py
"""

import os
import sys
import pathlib

ROOT = pathlib.Path(__file__).parent
import sqlite3
from datetime import datetime

from utils.database import get_db

try:
    from parser.queue_manager import (
        get_total_queue_count, get_queue_count, assign_post,
        remove_from_queue, remove_from_all_queues, create_queue
    )
    _QM = True
except ImportError:
    _QM = False

LOG_FILE = ROOT / "logs" / "app.log"

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
            in_queue = 0  # подставляется ниже
            checked = db.execute(
                "SELECT COUNT(*) FROM posts_info WHERE HumanChecked=1 AND Rejected=0"
            ).fetchone()[0]
            stuck = db.execute(
                "SELECT COUNT(*) FROM results r JOIN posts_info p ON p.ID = r.Post WHERE r.Reviewer IS NOT NULL AND r.HumanWords IS NULL AND p.Rejected = 0"
            ).fetchone()[0]
        in_queue  = get_total_queue_count() if _QM else 0
        stuck_str = f"  {R}🔒 Зависших: {stuck}{RESET}" if stuck else ""
        return (
            f"  {B}📋 В очереди: {in_queue}{RESET}   "
            f"{G}✅ Проверено: {checked}{RESET}"
            f"{stuck_str}"
        )
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# СТАТИСТИКА
# ══════════════════════════════════════════════════════════════════════════════

def show_stats():
    header("📊 Статистика")
    with get_db() as db:
        in_queue = get_total_queue_count() if _QM else 0
        checked   = db.execute(
            "SELECT COUNT(*) FROM posts_info WHERE HumanChecked=1 AND Rejected=0 AND PostOfReviewer=0"
        ).fetchone()[0]
        rejected  = db.execute(
            "SELECT COUNT(*) FROM posts_info WHERE Rejected=1 AND PostOfReviewer=0"
        ).fetchone()[0]
        total     = db.execute(
            "SELECT COUNT(*) FROM posts_info WHERE PostOfReviewer=0"
        ).fetchone()[0]
        reviewers = db.execute("SELECT COUNT(*) FROM reviewers").fetchone()[0]
        links     = db.execute("SELECT COUNT(*) FROM links").fetchone()[0]

        rows = db.execute(
            """
            SELECT rv.Name,
                   COUNT(CASE WHEN r.HumanWords IS NOT NULL THEN 1 END) as checked,
                   COUNT(CASE WHEN p.Rejected=1 AND r.Reviewer=rv.TGID THEN 1 END) as rejected
            FROM reviewers rv
            LEFT JOIN results    r ON r.Reviewer = rv.TGID
            LEFT JOIN posts_info p ON p.ID = r.Post
            GROUP BY rv.TGID
            ORDER BY checked DESC
            """
        ).fetchall()

    print(f"  {G}✅ Проверено:{RESET}      {checked}")
    print(f"  {B}📋 В очереди:{RESET}      {in_queue}")
    print(f"  {R}❌ Отклонено:{RESET}      {rejected}")
    print(f"  {W}📦 Всего постов:{RESET}   {total}")
    print(f"  {Y}🔗 Ссылок в БД:{RESET}    {links}")
    print(f"  {W}👥 Жюри:{RESET}           {reviewers}")

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
        lambda: set_admin(1), lambda: set_admin(0),
        delete_reviewer,
    ]
    while True:
        choice = menu("👥 Управление жюри", [
            "Список жюри",
            "Добавить жюри",
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
            "SELECT TGID, Name, URL, IsAdmin FROM reviewers ORDER BY Name"
        ).fetchall()
    if not rows:
        print(f"  {DIM}Жюри не зарегистрированы.{RESET}")
    else:
        print(f"  {'TGID':<15} {'Имя':<22} {'Адм':4}")
        hr()
        for r in rows:
            adm = f"{Y}★{RESET}" if r["IsAdmin"] else " "
            print(f"  {r['TGID']:<15} {r['Name']:<22} {adm}")
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
                "INSERT INTO reviewers (TGID, URL, Name, IsAdmin) VALUES (?, ?, ?, ?)",
                (tg_id, url, name, 1 if is_adm else 0),
            )
            db.commit()
            print(f"\n{G}  ✅ Жюри {name} добавлен.{RESET}")
        except sqlite3.IntegrityError:
            print(f"\n{R}  ❌ Такой tgID уже есть в БД.{RESET}")
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

def manage_posts():
    actions = [
        view_all_posts, view_queue, find_post, find_post_by_author,
        release_stuck, reset_post, reject_post_cli, restore_post,
        reassign_queue, clear_queue,
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
        ])
        if choice == -1:
            return
        actions[choice]()


def view_all_posts():
    header("📄 Все посты")
    status_opts = ["Все", "В очереди", "Проверены", "Отклонённые"]
    status_idx  = menu("Фильтр по статусу", status_opts)
    if status_idx == -1:
        return

    where = {
        0: "WHERE p.PostOfReviewer=0",
        1: "WHERE p.HumanChecked=0 AND p.Rejected=0 AND p.PostOfReviewer=0",
        2: "WHERE p.HumanChecked=1 AND p.Rejected=0 AND p.PostOfReviewer=0",
        3: "WHERE p.Rejected=1 AND p.PostOfReviewer=0",
    }[status_idx]

    with get_db() as db:
        rows = db.execute(
            f"""
            SELECT p.ID, p.URL, a.Name, p.HumanChecked, p.Rejected,
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
        print(f"  {'#':<6} {'Автор':<22} {'Статус':<12} {'Бот сл':>6} {'Чел сл':>7} {'Ош':>4}")
        hr()
        for r in rows:
            if r["Rejected"]:
                status = f"{R}отклонён{RESET}"
            elif r["HumanChecked"]:
                status = f"{G}проверен{RESET}"
            else:
                status = f"{Y}в очереди{RESET}"
            bot_w = str(r["BotWords"]   or "—")
            hum_w = str(r["HumanWords"] or "—")
            hum_e = str(r["HumanErrors"] or "—")
            print(f"  {r['ID']:<6} {r['Name']:<22} {status:<20} {bot_w:>6} {hum_w:>7} {hum_e:>4}")

        print()
        raw = input(f"  {W}ID поста для просмотра текста (0 — назад): {RESET}").strip()
        if not raw or raw == "0":
            return
        if not raw.isdigit():
            continue

        with get_db() as db:
            post = db.execute(
                """
                SELECT p.ID, p.URL, p.Text, a.Name, p.HumanChecked, p.Rejected,
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
        if post["Rejected"]:
            print(f"  {BOLD}Статус:{RESET} {R}Отклонён{RESET}  |  Причина: {post['RejectReason'] or '—'}")
        elif post["HumanChecked"]:
            print(f"  {BOLD}Статус:{RESET} {G}Проверен{RESET}  |  Слов: {post['HumanWords']}  Ошибок: {post['HumanErrors']}")
        else:
            print(f"  {BOLD}Статус:{RESET} {Y}В очереди{RESET}")
        print(f"  {BOLD}Бот слов:{RESET} {post['BotWords'] or '—'}")
        hr()
        text = post["Text"] or f"{DIM}(текст отсутствует){RESET}"
        for line in text.splitlines():
            print(f"  {line}")
        pause()


def view_queue():
    header("📋 Персональные очереди")
    with get_db() as db:
        reviewers = db.execute(
            "SELECT TGID, Name FROM reviewers WHERE Verified=1 ORDER BY Name"
        ).fetchall()

    if not reviewers:
        print(f"  {R}Нет верифицированных жюри.{RESET}")
        pause()
        return

    total = 0
    for rv in reviewers:
        count = get_queue_count(rv["TGID"]) if _QM else 0
        total += count
        bar = f"{B}{'█' * min(count, 20)}{RESET}" if count else f"{DIM}пусто{RESET}"
        print(f"  {rv['Name']:<22} {bar} {count}")

    hr()
    print(f"  Всего в очередях: {total}")
    pause()


def find_post():
    header("🔍 Найти пост по URL")
    url = input(f"  URL или часть: {W}").strip(); print(RESET, end="")
    with get_db() as db:
        rows = db.execute(
            """
            SELECT p.ID, p.URL, a.Name, p.HumanChecked, p.Rejected, r.BotWords
            FROM posts_info p
            JOIN authors    a ON p.Author = a.ID
            LEFT JOIN results r ON r.Post = p.ID
            WHERE p.URL LIKE ? AND p.PostOfReviewer=0
            LIMIT 10
            """,
            (f"%{url}%",),
        ).fetchall()

    if not rows:
        print(f"  {R}Не найдено.{RESET}")
    else:
        for r in rows:
            status = (
                f"{G}✅ проверен{RESET}"   if r["HumanChecked"] else
                f"{R}❌ отклонён{RESET}"   if r["Rejected"]     else
                f"{Y}⏳ в очереди{RESET}"
            )
            print(f"\n  {BOLD}#{r['ID']}{RESET} {r['Name']}")
            print(f"  {DIM}{r['URL']}{RESET}")
            print(f"  Статус: {status}  |  Бот: {r['BotWords'] or '—'} слов")
    pause()


def find_post_by_author():
    header("🔍 Найти посты по автору")
    name = input(f"  Имя автора или часть: {W}").strip(); print(RESET, end="")
    if not name:
        return
    with get_db() as db:
        rows = db.execute(
            """
            SELECT p.ID, p.URL, a.Name, p.HumanChecked, p.Rejected,
                   r.BotWords, r.HumanWords, r.HumanErrors
            FROM posts_info p
            JOIN authors    a ON p.Author = a.ID
            LEFT JOIN results r ON r.Post = p.ID
            WHERE a.Name LIKE ? AND p.PostOfReviewer=0
            ORDER BY p.ID DESC
            LIMIT 20
            """,
            (f"%{name}%",),
        ).fetchall()

    if not rows:
        print(f"  {R}Не найдено.{RESET}"); pause(); return

    header(f"🔍 Посты «{name}» ({len(rows)} шт.)")
    print(f"  {'#':<6} {'Автор':<22} {'Статус':<12} {'Бот сл':>6} {'Чел сл':>7} {'Ош':>4}")
    hr()
    for r in rows:
        if r["Rejected"]:
            status = f"{R}отклонён{RESET}"
        elif r["HumanChecked"]:
            status = f"{G}проверен{RESET}"
        else:
            status = f"{Y}в очереди{RESET}"
        print(
            f"  {r['ID']:<6} {r['Name']:<22} {status:<20} "
            f"{str(r['BotWords'] or '—'):>6} {str(r['HumanWords'] or '—'):>7} {str(r['HumanErrors'] or '—'):>4}"
        )
    pause()


def release_stuck():
    header("🔓 Сброс зависших постов")
    with get_db() as db:
        rows = db.execute(
            """
            SELECT r.ID as result_id, p.ID as post_id, p.URL, a.Name as author,
                   rv.Name as reviewer
            FROM results r
            JOIN posts_info p ON r.Post = p.ID
            JOIN authors    a ON p.Author = a.ID
            LEFT JOIN reviewers rv ON rv.TGID = r.Reviewer
            WHERE r.Reviewer IS NOT NULL AND r.HumanWords IS NULL AND p.Rejected=0
            """
        ).fetchall()

    if not rows:
        print(f"  {G}Зависших постов нет!{RESET}"); pause(); return

    print(f"  {Y}Найдено: {len(rows)}{RESET}\n")
    print(f"  {'#':<6} {'Автор поста':<22} {'Взял жюри':<20}  URL")
    hr()
    for r in rows:
        reviewer  = r["reviewer"] or f"tgID:{r['result_id']}"
        url_short = (r["URL"] or "")[-45:]
        print(f"  {r['post_id']:<6} {r['author']:<22} {reviewer:<20}  {DIM}{url_short}{RESET}")

    print()
    if not confirm(f"Освободить все {len(rows)} зависших постов?"):
        return

    with get_db() as db:
        db.execute(
            "UPDATE results SET Reviewer=NULL WHERE Reviewer IS NOT NULL AND HumanWords IS NULL"
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
        row = db.execute("SELECT URL FROM posts_info WHERE ID=?", (pid,)).fetchone()
        if not row:
            print(f"{R}  Пост #{pid} не найден.{RESET}"); pause(); return
        if not confirm(f"Сбросить проверку поста #{pid}?"):
            return
        db.execute(
            "UPDATE posts_info SET HumanChecked=0, Rejected=0 WHERE ID=?", (pid,)
        )
        db.execute(
            "UPDATE results SET HumanWords=NULL, HumanErrors=NULL, Reviewer=NULL, "
            "SkipReason=NULL, RejectReason=NULL WHERE Post=?",
            (pid,),
        )
        db.commit()
    if _QM:
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
            "UPDATE posts_info SET Rejected=1, HumanChecked=1 WHERE ID=?", (pid,)
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
        remove_from_all_queues(pid)

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
            WHERE p.Rejected=1 AND p.PostOfReviewer=0
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
            "UPDATE posts_info SET Rejected=0, HumanChecked=0 WHERE ID=?", (pid,)
        )
        db.execute(
            "UPDATE results SET HumanWords=NULL, HumanErrors=NULL, Reviewer=NULL, "
            "RejectReason=NULL WHERE Post=?",
            (pid,),
        )
        db.commit()
    if _QM:
        assign_post(pid)

    print(f"\n{G}  ✅ Пост #{pid} восстановлен и возвращён в очередь.{RESET}")
    pause()


def clear_queue():
    header("🗑  Очистить все очереди")
    total = get_total_queue_count() if _QM else 0

    if not confirm(f"Удалить ВСЕ {total} постов из очереди? (посты останутся в posts_info)"):
        return

    with get_db() as db:
        db.execute("DELETE FROM queue")
        db.commit()

    print(f"\n{G}  ✅ Все очереди очищены.{RESET}")
    pause()


def reassign_queue():
    """Очищает очередь и переназначает все непроверенные посты заново."""
    header("🔄 Переназначить очередь")

    with get_db() as db:
        post_count = db.execute(
            "SELECT COUNT(*) FROM posts_info WHERE HumanChecked=0 AND Rejected=0 AND PostOfReviewer=0"
        ).fetchone()[0]

    print(f"  Непроверенных постов: {BOLD}{post_count}{RESET}")
    print()
    print(f"  {Y}Что произойдёт:{RESET}")
    print(f"  1. Текущая очередь будет очищена")
    print(f"  2. Все {post_count} постов будут распределены заново по новой логике")
    print()

    if not confirm(f"Переназначить все {post_count} постов?"):
        return

    with get_db() as db:
        db.execute("DELETE FROM queue")
        db.commit()
        posts = db.execute(
            "SELECT ID FROM posts_info WHERE HumanChecked=0 AND Rejected=0 AND PostOfReviewer=0 ORDER BY ID"
        ).fetchall()

    if not _QM:
        print(f"  {R}queue_manager недоступен.{RESET}")
        pause()
        return

    print()
    ok = 0
    for p in posts:
        tgid = assign_post(p["ID"])
        if tgid:
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
    from pytz import timezone as _tz
    today = datetime.now(_tz("Europe/Moscow")).strftime("%d.%m.%Y")
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

    # Проверяем есть ли посты привязанные к этому дню
    with get_db() as db:
        post_count = db.execute(
            "SELECT COUNT(*) FROM posts_info WHERE Day = ?", (day_id,)
        ).fetchone()[0]

    target_day = None
    if post_count > 0:
        print(f"\n  {Y}⚠  К этому дню привязано {post_count} постов.{RESET}")

        # Выбираем куда перенести посты
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

    # Берём самый свежий лог файл
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
        ("results",    "Результаты"),
        # queue — заменена персональными очередями queue_{TGID}
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
# ГЛАВНОЕ МЕНЮ
# ══════════════════════════════════════════════════════════════════════════════

def main():
    actions = [
        show_stats, manage_reviewers, manage_posts,
        manage_days, manage_links, view_logs, full_reset,
    ]
    while True:
        choice = menu("Главное меню", [
            "📊 Статистика",
            "👥 Управление жюри",
            "📝 Управление постами",
            "📅 Управление днями",
            "🔗 Управление ссылками",
            "📄 Просмотр логов",
            "🗑  Полная очистка данных",
        ], show_stats=True)
        if choice == -1:
            clr()
            print(f"{DIM}Выход.{RESET}\n")
            break
        actions[choice]()


if __name__ == "__main__":
    main()
