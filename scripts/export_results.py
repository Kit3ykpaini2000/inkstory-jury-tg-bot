"""
export_results.py — экспорт итогов конкурса в Excel

Структура файла:
- Лист "Общий отчёт" — сводка по всем дням + статистика жюри
- Лист "ДД.ММ", "ДД.ММ", ... — принятые посты по каждому дню

Только принятые посты: HumanWords IS NOT NULL AND Rejected = 0

Запуск: python scripts/export_results.py
"""

import sys
import pathlib
import sqlite3
from datetime import datetime

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH     = ROOT / "data" / "main.db"
RESULTS_DIR = ROOT / "results"

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
except ImportError:
    print("❌ Не установлен openpyxl. Выполни: pip install openpyxl")
    sys.exit(1)


# ── Стили ─────────────────────────────────────────────────────────────────────

CLR_HEADER    = "2C3E50"
CLR_SUBHEADER = "5D6D7E"
CLR_ACCENT    = "3498DB"
CLR_EVEN      = "EBF5FB"
CLR_WHITE     = "FFFFFF"
CLR_SECTION   = "D6EAF8"
CLR_GREEN     = "1E8449"
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


# ── БД ────────────────────────────────────────────────────────────────────────

def _connect():
    if not DB_PATH.exists():
        print(f"❌ База данных не найдена: {DB_PATH}")
        print("   Запусти сначала: python scripts/init_db.py")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_days(conn):
    return conn.execute("SELECT Day, Data FROM days ORDER BY Day").fetchall()


def _get_posts_by_day(conn, day_id):
    """Принятые посты за день сгруппированные по автору."""
    return conn.execute(
        """
        SELECT
            a.Name                      AS author,
            COUNT(p.ID)                 AS post_count,
            COALESCE(SUM(r.HumanWords), 0)  AS words,
            COALESCE(SUM(r.HumanErrors), 0) AS errors
        FROM posts_info p
        JOIN authors  a ON p.Author   = a.ID
        JOIN results  r ON r.Post     = p.ID
        WHERE p.Day          = ?
          AND p.Rejected      = 0
          AND p.PostOfReviewer = 0
          AND r.HumanWords   IS NOT NULL
        GROUP BY a.ID
        ORDER BY errors * 1.0 / NULLIF(words, 0) ASC
        """,
        (day_id,),
    ).fetchall()


def _get_summary_by_day(conn):
    return conn.execute(
        """
        SELECT
            d.Data                                         AS date,
            COUNT(p.ID)                                    AS posts,
            COALESCE(SUM(r.HumanWords), 0)                AS words,
            COALESCE(SUM(r.HumanErrors), 0)               AS errors
        FROM days d
        LEFT JOIN posts_info p ON p.Day = d.Day
            AND p.Rejected = 0 AND p.PostOfReviewer = 0
        LEFT JOIN results r ON r.Post = p.ID AND r.HumanWords IS NOT NULL
        GROUP BY d.Day ORDER BY d.Day
        """
    ).fetchall()


def _get_reviewer_stats(conn):
    return conn.execute(
        """
        SELECT
            rv.Name,
            COUNT(r.ID)                        AS checked,
            COALESCE(SUM(r.HumanWords), 0)     AS words,
            COALESCE(SUM(r.HumanErrors), 0)    AS errors
        FROM reviewers rv
        LEFT JOIN results r ON r.Reviewer = rv.TGID AND r.HumanWords IS NOT NULL
        WHERE rv.Verified = 1
        GROUP BY rv.TGID ORDER BY checked DESC
        """
    ).fetchall()


# ── Лист: Общий отчёт ─────────────────────────────────────────────────────────

def _build_summary_sheet(ws, conn):
    ws.title = "Общий отчёт"
    ws.sheet_view.showGridLines = False

    # Заголовок
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

    # ── Таблица по дням ───────────────────────────────────────────────────────
    ws.row_dimensions[3].height = 10

    ws.merge_cells("A4:F4")
    ws["A4"].value     = "ПО ДНЯМ КОНКУРСА"
    ws["A4"].font      = _font(11, bold=True, color=CLR_LIGHT)
    ws["A4"].fill      = _fill(CLR_ACCENT)
    ws["A4"].alignment = _align("center")
    ws.row_dimensions[4].height = 24

    headers = ["Дата", "Принято постов", "Слов (жюри)", "Ошибок", "Ошибок / 1000 слов", ""]
    for c, h in enumerate(headers, 1):
        ws.cell(row=5, column=c).value = h
    _header_row(ws, 5, 5)

    days = _get_summary_by_day(conn)
    for i, d in enumerate(days, 1):
        r = 5 + i
        ep1k = f"=IFERROR(ROUND(D{r}/C{r}*1000,1),0)"
        vals = [d["date"], d["posts"], d["words"], d["errors"], ep1k]
        for c, v in enumerate(vals, 1):
            ws.cell(row=r, column=c).value = v
        _data_row(ws, r, 5, even=(i % 2 == 0))

    # Итого
    first = 6
    last  = 5 + len(days)
    tr    = last + 1
    _totals_row(ws, tr, 5)
    ws.cell(row=tr, column=1).value = "ИТОГО"
    ws.cell(row=tr, column=2).value = f"=SUM(B{first}:B{last})"
    ws.cell(row=tr, column=3).value = f"=SUM(C{first}:C{last})"
    ws.cell(row=tr, column=4).value = f"=SUM(D{first}:D{last})"
    ws.cell(row=tr, column=5).value = f"=IFERROR(ROUND(D{tr}/C{tr}*1000,1),0)"

    # ── Статистика жюри ───────────────────────────────────────────────────────
    r2 = tr + 2
    ws.merge_cells(f"A{r2}:F{r2}")
    ws[f"A{r2}"].value     = "СТАТИСТИКА ЖЮРИ"
    ws[f"A{r2}"].font      = _font(11, bold=True, color=CLR_LIGHT)
    ws[f"A{r2}"].fill      = _fill(CLR_ACCENT)
    ws[f"A{r2}"].alignment = _align("center")
    ws.row_dimensions[r2].height = 24

    r2 += 1
    rev_headers = ["Жюри", "Проверено постов", "Слов проверено", "Ошибок найдено", "Ошибок / 1000 слов", ""]
    for c, h in enumerate(rev_headers, 1):
        ws.cell(row=r2, column=c).value = h
    _header_row(ws, r2, 5)

    for i, rv in enumerate(_get_reviewer_stats(conn), 1):
        r2 += 1
        ep1k = f"=IFERROR(ROUND(D{r2}/C{r2}*1000,1),0)"
        vals = [rv["Name"], rv["checked"], rv["words"], rv["errors"], ep1k]
        for c, v in enumerate(vals, 1):
            ws.cell(row=r2, column=c).value = v
        _data_row(ws, r2, 5, even=(i % 2 == 0))

    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 22
    ws.column_dimensions["F"].width = 4


# ── Лист: День ────────────────────────────────────────────────────────────────

def _build_day_sheet(ws, day_row, posts):
    ws.title = day_row["Data"]
    ws.sheet_view.showGridLines = False

    # Заголовок
    ws.merge_cells("A1:F1")
    ws["A1"].value     = day_row["Data"]
    ws["A1"].font      = _font(14, bold=True, color=CLR_LIGHT)
    ws["A1"].fill      = _fill(CLR_HEADER)
    ws["A1"].alignment = _align("center")
    ws.row_dimensions[1].height = 30

    ws.row_dimensions[2].height = 8

    # Заголовки таблицы — точно как в образце
    headers = ["Юзер", "Постов", "Слов", "Ошибок", "Ошибок / 1000 слов", ""]
    for c, h in enumerate(headers, 1):
        ws.cell(row=3, column=c).value = h
    _header_row(ws, 3, 5)

    if not posts:
        ws.merge_cells("A4:F4")
        ws["A4"].value     = "Нет данных за этот день"
        ws["A4"].font      = _font(color="888888")
        ws["A4"].alignment = _align("center")
        return

    for i, p in enumerate(posts, 1):
        r = 3 + i
        ep1k = f"=IFERROR(ROUND(D{r}/C{r}*1000,1),0)"
        vals = [p["author"], p["post_count"], p["words"], p["errors"], ep1k]
        for c, v in enumerate(vals, 1):
            ws.cell(row=r, column=c).value = v
        _data_row(ws, r, 5, even=(i % 2 == 0))

    # Итого
    first = 4
    last  = 3 + len(posts)
    tr    = last + 1
    _totals_row(ws, tr, 5)
    ws.cell(row=tr, column=1).value = "ИТОГО"
    ws.cell(row=tr, column=2).value = f"=SUM(B{first}:B{last})"
    ws.cell(row=tr, column=3).value = f"=SUM(C{first}:C{last})"
    ws.cell(row=tr, column=4).value = f"=SUM(D{first}:D{last})"
    ws.cell(row=tr, column=5).value = f"=IFERROR(ROUND(D{tr}/C{tr}*1000,1),0)"

    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 10
    ws.column_dimensions["C"].width = 12
    ws.column_dimensions["D"].width = 12
    ws.column_dimensions["E"].width = 22
    ws.column_dimensions["F"].width = 4


# ── Точка входа ───────────────────────────────────────────────────────────────

def export() -> pathlib.Path:
    conn = _connect()
    days = _get_days(conn)

    wb = Workbook()
    wb.remove(wb.active)

    ws_summary = wb.create_sheet("Общий отчёт")
    _build_summary_sheet(ws_summary, conn)

    for day in days:
        posts  = _get_posts_by_day(conn, day["Day"])
        ws_day = wb.create_sheet()
        _build_day_sheet(ws_day, day, posts)

    conn.close()

    RESULTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out_path  = RESULTS_DIR / f"results_{timestamp}.xlsx"
    wb.save(out_path)
    return out_path


def main():
    print("📊 Экспорт итогов конкурса...")
    out = export()
    print(f"✅ Готово: {out}")


if __name__ == "__main__":
    main()