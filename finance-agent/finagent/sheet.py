"""Your budget as an Excel workbook you edit; finagent reads it back on every run.

Tabs
  Budget      one row per category. Edit the yellow "Monthly budget" column. Live totals at the top:
              regular income, total budget, left over, savings rate vs your target.
  Income      one row per income source with a Yes/No "Regular income?" dropdown. Only "Yes" counts
              toward the budget — mark one-time money (tax refunds, settlements) "No".
  How to use  short instructions.

`finagent sheet` creates the workbook, or refreshes its reference columns (recent averages) while keeping
everything you typed: budgets, notes, Yes/No choices, and any rows you added yourself.
Needs the `openpyxl` package (python3 -m pip install openpyxl).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from finagent.budgeting import Suggestion
from finagent.ledger import Ledger
from finagent.util import month_end, month_label, month_start, parse_amount

BUDGET, INCOME, HELP = "Budget", "Income", "How to use"
BUDGET_HEADERS = ["Group", "Category", "Monthly budget", "Avg last 3 mo", "Avg last 6 mo", "Last month",
                  "Budget vs. recent", "Type", "Notes"]
INCOME_HEADERS = ["Source", "Category", "Deposits", "Avg per month", "Total", "Last deposit", "Regular income?",
                  "Notes"]
BUDGET_HEADER_ROW, INCOME_HEADER_ROW = 11, 5
ACCENT, YELLOW, GREY, WHITE = "1D6B57", "FFF4CC", "F1F3F5", "FFFFFF"


class SheetUnavailable(RuntimeError):
    pass


def require_openpyxl():
    try:
        import openpyxl
    except ImportError:
        raise SheetUnavailable(
            "The budget spreadsheet needs the free openpyxl package. Install it once with:\n"
            "    python3 -m pip install openpyxl        (Windows: py -m pip install openpyxl)") from None
    return openpyxl


def sheet_path(cfg) -> Path:
    return cfg.resolve((cfg.get("budget_sheet") or {}).get("path", "budget.xlsx"))


@dataclass
class SheetData:
    budgets: dict[str, float] = field(default_factory=dict)
    blank: set[str] = field(default_factory=set)  # categories listed with no budget typed in
    groups: dict[str, str] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    income: dict[str, bool] = field(default_factory=dict)
    income_notes: dict[str, str] = field(default_factory=dict)
    target_rate: float | None = None


def _yes(value) -> bool | None:
    if isinstance(value, bool):
        return value
    v = str(value or "").strip().lower()
    if v in ("yes", "y", "true", "1", "regular"):
        return True
    if v in ("no", "n", "false", "0", "one-time", "one time"):
        return False
    return None


def _header_index(ws, row: int) -> dict[str, int]:
    return {str(c.value).strip(): c.column for c in ws[row] if c.value}


def _find_header(ws, needed: tuple[str, ...], default: int) -> int:
    for r in range(1, min(ws.max_row, 40) + 1):
        values = {str(c.value).strip() for c in ws[r] if c.value}
        if all(n in values for n in needed):
            return r
    return default


def read_sheet(path: Path) -> SheetData:
    openpyxl = require_openpyxl()
    wb = openpyxl.load_workbook(path, data_only=True)
    data = SheetData()
    if BUDGET in wb.sheetnames:
        ws = wb[BUDGET]
        hr = _find_header(ws, ("Category", "Monthly budget"), BUDGET_HEADER_ROW)
        col = _header_index(ws, hr)
        for r in range(hr + 1, ws.max_row + 1):
            cat = ws.cell(r, col["Category"]).value
            if not cat or not str(cat).strip():
                continue
            cat = str(cat).strip()
            raw = ws.cell(r, col["Monthly budget"]).value
            amount = raw if isinstance(raw, (int, float)) and not isinstance(raw, bool) else parse_amount(raw)
            if amount is None:
                data.blank.add(cat)
            elif amount > 0:
                data.budgets[cat] = float(amount)
            if "Group" in col and ws.cell(r, col["Group"]).value:
                data.groups[cat] = str(ws.cell(r, col["Group"]).value).strip()
            if "Notes" in col and ws.cell(r, col["Notes"]).value:
                data.notes[cat] = str(ws.cell(r, col["Notes"]).value)
        for r in range(1, hr):
            label = str(ws.cell(r, 1).value or "").strip().lower()
            if label.startswith("target savings rate"):
                v = ws.cell(r, 3).value
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    data.target_rate = float(v) / 100 if v > 1 else float(v)
    if INCOME in wb.sheetnames:
        ws = wb[INCOME]
        hr = _find_header(ws, ("Source", "Regular income?"), INCOME_HEADER_ROW)
        col = _header_index(ws, hr)
        for r in range(hr + 1, ws.max_row + 1):
            src = ws.cell(r, col["Source"]).value
            if not src or not str(src).strip():
                continue
            choice = _yes(ws.cell(r, col["Regular income?"]).value)
            if choice is not None:
                data.income[str(src).strip()] = choice
            if "Notes" in col and ws.cell(r, col["Notes"]).value:
                data.income_notes[str(src).strip()] = str(ws.cell(r, col["Notes"]).value)
    return data


def apply_sheet(cfg) -> str | None:
    """If the workbook exists, make it the source of budgets and income choices. Returns a warning or None."""
    path = sheet_path(cfg)
    if not path.exists():
        return None
    try:
        data = read_sheet(path)
    except SheetUnavailable as err:
        return f"Found {path.name}, but can't read it yet. {err}\nUsing the budgets in config.toml for now."
    except Exception as err:  # a half-saved or non-Excel file shouldn't break reports
        return f"Couldn't read {path.name} ({err}). Using the budgets in config.toml for now."
    if data.budgets or data.blank:
        cfg["budgets"] = dict(data.budgets)
    income = cfg.setdefault("income", {})
    income["sources"] = {**(income.get("sources") or {}), **data.income}
    if data.target_rate:
        cfg["analysis"]["savings_rate_target"] = data.target_rate
    cfg["_sheet"] = str(path)
    return None


# --------------------------------------------------------------------------- writing

def write_sheet(path: Path, L: Ledger, suggestions: list[Suggestion], months6: list[str], months3: list[str],
                currency: str = "$", existing: SheetData | None = None, config_budgets: dict | None = None,
                target_rate: float = 0.15) -> Path:
    """Create or refresh the workbook. Your typed values in `existing` always win over suggestions."""
    openpyxl = require_openpyxl()
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation

    existing = existing or SheetData()
    config_budgets = config_budgets or {}
    money = f'"{currency}"#,##0'
    money_signed = f'"{currency}"#,##0;[Red]-"{currency}"#,##0'
    bold, white_bold = Font(bold=True), Font(bold=True, color=WHITE)
    accent_fill, yellow_fill, grey_fill = (PatternFill("solid", fgColor=c) for c in (ACCENT, YELLOW, GREY))
    thin = Border(bottom=Side(style="thin", color="E3E7EC"))
    wrap = Alignment(wrap_text=True, vertical="top")

    def avg(cat: str, months: list[str]) -> float:
        if not months:
            return 0.0
        start, end = month_start(months[0]), month_end(months[-1])
        return sum(t.outflow for t in L.expenses_between(start, end) if t.category == cat) / len(months)

    last_month = months6[-1:] if months6 else []
    by_cat = {s.category: s for s in suggestions}
    cats: list[str] = []
    for s in suggestions:
        cats.append(s.category)
    for c in list(existing.budgets) + sorted(existing.blank) + list(config_budgets):
        if c not in cats:
            cats.append(c)

    def kind(cat: str) -> str:
        if cat in by_cat:
            return by_cat[cat].kind
        if L.is_protected(cat):
            return "giving"
        return "your row"

    def group(cat: str) -> str:
        return existing.groups.get(cat) or (by_cat[cat].group if cat in by_cat else L.group_of(cat))

    def budget_value(cat: str):
        if cat in existing.budgets:
            return existing.budgets[cat]
        if cat in existing.blank:
            return None
        if cat in config_budgets:
            return float(config_budgets[cat])
        return by_cat[cat].target if cat in by_cat else None

    rows = sorted(cats, key=lambda c: (group(c), -avg(c, months3)))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = BUDGET
    period = (f"{month_label(months6[0])} – {month_label(months6[-1])}" if months6 else "your data")
    ws["A1"] = "Household budget"
    ws["A1"].font = Font(bold=True, size=16)
    ws["A2"] = ("Edit the yellow cells, then save (keep it as an Excel .xlsx file). Every finagent report reads this "
                "file automatically. Income comes from the Income tab — only sources marked Yes count.")
    ws["A2"].alignment = wrap
    ws.merge_cells("A2:I2")
    ws.row_dimensions[2].height = 32

    first, last = BUDGET_HEADER_ROW + 1, BUDGET_HEADER_ROW + max(len(rows), 1) + 200
    income_rng = f"Income!$D${INCOME_HEADER_ROW + 1}:$D$1000"
    regular_rng = f"Income!$G${INCOME_HEADER_ROW + 1}:$G$1000"
    summary = [
        ("Regular monthly income", f'=SUMIFS({income_rng},{regular_rng},"Yes")', money, False),
        ("Total monthly budget", f"=SUM(C{first}:C{last})", money, False),
        ("Left over each month", "=C4-C5", money_signed, False),
        ("Savings rate", "=IF(C4>0,C6/C4,0)", "0%", False),
        ("Target savings rate", target_rate, "0%", True),
        ("Still to find to hit the target", "=MAX(0,C4*C8-C6)", money, False),
    ]
    for i, (label, value, fmt, editable) in enumerate(summary):
        r = 4 + i
        ws.cell(r, 1, label).font = bold
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
        c = ws.cell(r, 3, value)
        c.number_format, c.font = fmt, bold
        if editable:
            c.fill = yellow_fill
    ws.cell(10, 1, f"Averages are from {period}. Transfers and card payments are excluded; one-time "
                   f"purchases are included in the averages but covered by an 'Irregular Expenses' line.").font = \
        Font(italic=True, color="5D6B79")

    for j, h in enumerate(BUDGET_HEADERS, 1):
        c = ws.cell(BUDGET_HEADER_ROW, j, h)
        c.font, c.fill = white_bold, accent_fill
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for i, cat in enumerate(rows):
        r = first + i
        values = [group(cat), cat, budget_value(cat), round(avg(cat, months3), 2), round(avg(cat, months6), 2),
                  round(avg(cat, last_month), 2), f'=IF(C{r}="","",C{r}-D{r})', kind(cat),
                  existing.notes.get(cat, "")]
        for j, v in enumerate(values, 1):
            c = ws.cell(r, j, v)
            c.border = thin
            if j in (3, 4, 5, 6):
                c.number_format = money
            if j == 7:
                c.number_format = money_signed
        ws.cell(r, 3).fill = yellow_fill
        ws.cell(r, 9).fill = yellow_fill
        if kind(cat) in ("fixed", "giving", "sinking fund"):
            for j in (1, 2, 8):
                ws.cell(r, j).fill = grey_fill
    for col, width in zip("ABCDEFGHI", (16, 26, 15, 14, 14, 12, 16, 13, 40)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = ws.cell(first, 3)

    # ---------------------------------------------------------------- Income tab
    wi = wb.create_sheet(INCOME)
    wi["A1"] = "Income"
    wi["A1"].font = Font(bold=True, size=16)
    wi["A2"] = ("Regular income? — choose Yes for money you can count on every month (your paychecks). Choose No "
                "for one-time money such as tax refunds, settlements, or gifts: it's shown in reports but never "
                "counted as income you can budget.")
    wi["A2"].alignment = wrap
    wi.merge_cells("A2:H2")
    wi.row_dimensions[2].height = 46
    n6 = max(len(months6), 1)
    wi["A3"] = f"Deposits, totals and averages cover {period} ({n6} months)."
    wi["A3"].font = Font(italic=True, color="5D6B79")
    for j, h in enumerate(INCOME_HEADERS, 1):
        c = wi.cell(INCOME_HEADER_ROW, j, h)
        c.font, c.fill = white_bold, accent_fill
    start6 = month_start(months6[0]) if months6 else date.min
    end6 = month_end(months6[-1]) if months6 else date.max
    sources = L.income_sources(start6, end6)
    listed = {s["source"] for s in sources}
    for src, choice in existing.income.items():  # keep sources you classified even if quiet lately
        if src not in listed:
            sources.append({"source": src, "category": "", "count": 0, "total": 0.0, "last": None,
                            "regular": choice})
    dv = DataValidation(type="list", formula1='"Yes,No"', allow_blank=False)
    wi.add_data_validation(dv)
    for i, s in enumerate(sources):
        r = INCOME_HEADER_ROW + 1 + i
        choice = existing.income.get(s["source"], s["regular"])
        vals = [s["source"], s["category"], s["count"], f"=E{r}/{n6}", round(s["total"], 2), s["last"],
                "Yes" if choice else "No", existing.income_notes.get(s["source"], "")]
        for j, v in enumerate(vals, 1):
            c = wi.cell(r, j, v)
            c.border = thin
            if j in (4, 5):
                c.number_format = money
            if j == 6 and v:
                c.number_format = "mmm d, yyyy"
        wi.cell(r, 7).fill = yellow_fill
        wi.cell(r, 8).fill = yellow_fill
        dv.add(wi.cell(r, 7))
    for col, width in zip("ABCDEFGH", (30, 18, 10, 14, 14, 14, 16, 36)):
        wi.column_dimensions[col].width = width
    wi.freeze_panes = wi.cell(INCOME_HEADER_ROW + 1, 1)

    # ---------------------------------------------------------------- How to use
    wh = wb.create_sheet(HELP)
    lines = [
        ("How to use this budget", True),
        ("1. Income tab: mark each source Yes (regular) or No (one-time). Only Yes income counts.", False),
        ("2. Budget tab: type your monthly budget in the yellow column. Leave a cell empty for no budget.", False),
        ("   'Budget vs. recent' shows how far each budget is from what you actually spent lately", False),
        ("   (red = you'll need to spend less than you have been).", False),
        ("3. Watch the totals at the top: aim for 'Left over each month' to reach your target savings rate.", False),
        ("4. Add your own category: type it in a new row at the bottom. The name must match the category in", False),
        ("   your reports exactly (for example 'Dining Out').", False),
        ("5. Save as Excel (.xlsx). In Numbers, use File → Export To → Excel and replace this file.", False),
        ("6. Run any finagent report — it reads this file automatically. Run `finagent sheet` (with this", False),
        ("   file closed) to refresh the averages; your numbers, notes and Yes/No choices are kept.", False),
        ("Giving and saving lines are yours to set — finagent never suggests cutting them.", False),
    ]
    for i, (text, is_title) in enumerate(lines, 1):
        wh.cell(i, 1, text).font = Font(bold=True, size=14) if is_title else Font()
    wh.column_dimensions["A"].width = 110

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy(path, path.with_name(path.stem + ".backup.xlsx"))
    wb.save(path)
    return path


def set_budgets(path: Path, changes: dict[str, float]) -> list[tuple[str, float | None, float]]:
    """Change budget cells in the workbook (0 clears one). Adds a row for a category that isn't listed."""
    openpyxl = require_openpyxl()
    from openpyxl.styles import PatternFill
    wb = openpyxl.load_workbook(path)
    ws = wb[BUDGET]
    hr = _find_header(ws, ("Category", "Monthly budget"), BUDGET_HEADER_ROW)
    col = _header_index(ws, hr)
    c_cat, c_bud = col["Category"], col["Monthly budget"]
    report = []
    for name, amount in changes.items():
        row = next((r for r in range(hr + 1, ws.max_row + 1)
                    if str(ws.cell(r, c_cat).value or "").strip().lower() == name.lower()), None)
        if row is None:
            row = max((r for r in range(hr + 1, ws.max_row + 1) if ws.cell(r, c_cat).value), default=hr) + 1
            ws.cell(row, c_cat, name)
            if "Group" in col:
                ws.cell(row, col["Group"], "Other")
            ws.cell(row, c_bud).number_format = ws.cell(hr + 1, c_bud).number_format
            ws.cell(row, c_bud).fill = PatternFill("solid", fgColor=YELLOW)
            old = None
        else:
            v = ws.cell(row, c_bud).value
            old = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        ws.cell(row, c_bud, amount if amount > 0 else None)
        report.append((str(ws.cell(row, c_cat).value), old, amount))
    wb.save(path)
    return report
