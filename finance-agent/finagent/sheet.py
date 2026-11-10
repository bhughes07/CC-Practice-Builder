"""Your budget as an Excel workbook you edit; finagent reads it back on every run.

Tabs
  Budget      your budget lines, in your order. Edit the yellow cells:
                Monthly budget — the plan for the line
                Matches        — words that identify its transactions (merchant names like "AT&T, Hulu", or a
                                 bank category like "Groceries"). finagent files every transaction under the
                                 line it matches, so spending is sorted automatically.
              Below your lines, "Not in your plan yet" lists real spending that matched no line, with averages.
              Live totals at the top: regular income, total budget, left over, savings rate vs target.
  Income      one row per income source with a Yes/No "Regular income?" dropdown. Only "Yes" counts.
  How to use  short instructions.

`finagent sheet` creates the workbook or refreshes its reference columns, keeping everything you typed.
`finagent sheet --import FILE` replaces the budget lines with another workbook's (e.g. a starter built from
your old budgeting app), keeping your Income choices. Needs `openpyxl` (python3 -m pip install openpyxl).
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from finagent.budgeting import Suggestion
from finagent.categorize import why_here
from finagent.ledger import Ledger
from finagent.models import CLASS_DEBT, CLASS_ESSENTIAL, CLASS_FEES, CLASS_GIVING, CLASS_SAVINGS, KIND_EXPENSE
from finagent.util import month_end, month_label, month_start, parse_amount

BUDGET, INCOME, TXNS, LISTS, HELP = "Budget", "Income", "Transactions", "Lists", "How to use"
TXN_HEADERS = ["Date", "Budget line", "Merchant", "Bank description", "Account", "Amount", "Bank category",
               "Why it's here", "Move to line", "Note", "ID"]
TXN_WIDTHS = (13, 24, 24, 44, 20, 12, 20, 22, 22, 30, 4)
TXN_HEADER_ROW = 4
BUDGET_HEADERS = ["Group", "Budget line", "Monthly budget", "Matches", "Avg last 3 mo", "Avg last 6 mo",
                  "Last month", "Budget vs. recent", "Type", "Notes"]
INCOME_HEADERS = ["Source", "Category", "Deposits", "Avg per month", "Total", "Last deposit", "Regular income?",
                  "Notes"]
BUDGET_HEADER_ROW, INCOME_HEADER_ROW = 15, 5
NOT_IN_PLAN, SEPARATOR = "not in plan", "Not in your plan yet"
# Notes written by earlier versions of finagent that are no longer true; replaced on refresh.
OUTDATED_NOTES = {
    "Card payments are transfers in finagent: purchases on the card are already counted line by line. "
    "Keep a budget here only for paying down an old balance.":
        "Card payments from checking land here automatically (for cards you haven't connected).",
    "Giving totaled $1,166 in February. Add the other giving line(s) below this one.":
        "Your giving goal line — set it first each month.",
}
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
    lines: list[str] = field(default_factory=list)  # your budget lines, in your order
    budgets: dict[str, float] = field(default_factory=dict)
    blank: set[str] = field(default_factory=set)  # lines with no budget typed in (a typed 0 is a $0 budget)
    groups: dict[str, str] = field(default_factory=dict)
    matches: dict[str, list[str]] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    income: dict[str, bool] = field(default_factory=dict)
    income_notes: dict[str, str] = field(default_factory=dict)
    target_rate: float | None = None
    giving_goal: float | None = None
    overrides: dict[str, str] = field(default_factory=dict)  # transaction id → line, or "Exclude"
    txn_notes: dict[str, str] = field(default_factory=dict)


def _yes(value) -> bool | None:
    if isinstance(value, bool):
        return value
    v = str(value or "").strip().lower()
    if v in ("yes", "y", "true", "1", "regular"):
        return True
    if v in ("no", "n", "false", "0", "one-time", "one time"):
        return False
    return None


def _number(raw) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    return parse_amount(raw)


def _header_index(ws, row: int) -> dict[str, int]:
    return {str(c.value).strip(): c.column for c in ws[row] if c.value}


def _find_header(ws, needed: tuple[str, ...], default: int, alternatives: tuple[str, ...] = ()) -> int:
    for r in range(1, min(ws.max_row, 40) + 1):
        values = {str(c.value).strip() for c in ws[r] if c.value}
        if all(n in values for n in needed) and (not alternatives or values & set(alternatives)):
            return r
    return default


def split_terms(text) -> list[str]:
    return [t.strip() for t in re.split(r"[,;\n]", str(text or "")) if t.strip()]


def read_sheet(path: Path) -> SheetData:
    openpyxl = require_openpyxl()
    wb = openpyxl.load_workbook(path, data_only=True)
    data = SheetData()
    if BUDGET in wb.sheetnames:
        ws = wb[BUDGET]
        hr = _find_header(ws, ("Monthly budget",), BUDGET_HEADER_ROW, ("Budget line", "Category"))
        col = _header_index(ws, hr)
        c_line = col.get("Budget line") or col.get("Category")
        for r in range(hr + 1, ws.max_row + 1):
            line = ws.cell(r, c_line).value
            if not line or not str(line).strip():
                continue
            line = str(line).strip()
            amount = _number(ws.cell(r, col["Monthly budget"]).value)
            terms = split_terms(ws.cell(r, col["Matches"]).value) if "Matches" in col else []
            note = str(ws.cell(r, col["Notes"]).value or "").strip() if "Notes" in col else ""
            kind = str(ws.cell(r, col["Type"]).value or "").strip().lower() if "Type" in col else ""
            if kind == NOT_IN_PLAN and amount is None and not terms and not note:
                continue  # informational row we wrote, untouched by you
            if line not in data.lines:
                data.lines.append(line)
            if amount is None or amount < 0:
                data.blank.add(line)
            else:
                data.budgets[line] = amount  # a typed 0 is a real plan: spend nothing here
            if terms:
                data.matches[line] = terms
            if note:
                data.notes[line] = note
            if "Group" in col and ws.cell(r, col["Group"]).value:
                data.groups[line] = str(ws.cell(r, col["Group"]).value).strip()
        for r in range(1, hr):
            label = str(ws.cell(r, 1).value or "").strip().lower()
            if label.startswith("target savings rate"):
                v = _number(ws.cell(r, 3).value)
                if v:
                    data.target_rate = v / 100 if v > 1 else v
            if label.startswith("giving goal (%"):
                v = _number(ws.cell(r, 3).value)
                if v is not None:
                    data.giving_goal = v / 100 if v > 1 else v
    if TXNS in wb.sheetnames:
        ws = wb[TXNS]
        hr = _find_header(ws, ("Move to line", "ID"), TXN_HEADER_ROW)
        col = _header_index(ws, hr)
        for r in range(hr + 1, ws.max_row + 1):
            tid = ws.cell(r, col["ID"]).value
            if not tid:
                continue
            move = str(ws.cell(r, col["Move to line"]).value or "").strip()
            note = str(ws.cell(r, col["Note"]).value or "").strip() if "Note" in col else ""
            if move:
                data.overrides[str(tid)] = move
            if note:
                data.txn_notes[str(tid)] = note
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


def merge_import(existing: SheetData | None, incoming: SheetData) -> SheetData:
    """Budget lines, savings target and giving goal come from `incoming`. The Income tab never does: it's rebuilt
    from your real deposits, keeping the Yes/No choices already in `existing`."""
    merged = SheetData(lines=list(incoming.lines), budgets=dict(incoming.budgets), blank=set(incoming.blank),
                       groups=dict(incoming.groups), matches=dict(incoming.matches), notes=dict(incoming.notes),
                       target_rate=incoming.target_rate, giving_goal=incoming.giving_goal,
                       overrides={**(existing.overrides if existing else {}), **incoming.overrides},
                       txn_notes={**(existing.txn_notes if existing else {}), **incoming.txn_notes})
    merged.income, merged.income_notes = dict(incoming.income), dict(incoming.income_notes)
    if existing:  # your current choices, unless the imported file says otherwise
        merged.income = {**existing.income, **incoming.income}
        merged.income_notes = {**existing.income_notes, **incoming.income_notes}
        merged.target_rate = merged.target_rate or existing.target_rate
        merged.giving_goal = merged.giving_goal if merged.giving_goal is not None else existing.giving_goal
    return merged


def configure(cfg, data: SheetData) -> None:
    """Make sheet data the source of budgets, budget-line matching, income choices and savings target."""
    if data.lines:
        cfg["budgets"] = dict(data.budgets)
    cats = cfg.setdefault("categories", {})
    cats["line_rules"] = [{"line": line, "group": data.groups.get(line, ""), "terms": data.matches[line]}
                          for line in data.lines if data.matches.get(line)]
    cats["line_groups"] = dict(data.groups)
    cats["txn_overrides"] = dict(data.overrides)
    income = cfg.setdefault("income", {})
    income["sources"] = {**(income.get("sources") or {}), **data.income}
    if data.target_rate:
        cfg["analysis"]["savings_rate_target"] = data.target_rate
    if data.giving_goal is not None:
        cfg["analysis"]["giving_goal"] = data.giving_goal


def apply_sheet(cfg) -> str | None:
    """If the workbook exists, make it the source of truth. Returns a warning or None."""
    path = sheet_path(cfg)
    if not path.exists():
        return None
    try:
        data = read_sheet(path)
    except SheetUnavailable as err:
        return f"Found {path.name}, but can't read it yet. {err}\nUsing the budgets in config.toml for now."
    except Exception as err:  # a half-saved or non-Excel file shouldn't break reports
        return f"Couldn't read {path.name} ({err}). Using the budgets in config.toml for now."
    configure(cfg, data)
    cfg["_sheet"] = str(path)
    return None


# --------------------------------------------------------------------------- writing

def write_sheet(path: Path, L: Ledger, suggestions: list[Suggestion], months6: list[str], months3: list[str],
                currency: str = "$", existing: SheetData | None = None, config_budgets: dict | None = None,
                target_rate: float = 0.15, giving_goal: float = 0.0) -> Path:
    """Create or refresh the workbook. What you typed (in `existing`) always wins over suggestions.
    `L` must already be built with the budget-line matching in effect, so averages are per line."""
    openpyxl = require_openpyxl()
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.utils import get_column_letter

    existing = existing or SheetData()
    config_budgets = config_budgets or {}
    money = f'"{currency}"#,##0'
    money_cents = f'"{currency}"#,##0.00'
    money_signed = f'"{currency}"#,##0;[Red]-"{currency}"#,##0'
    bold, white_bold = Font(bold=True), Font(bold=True, color=WHITE)
    accent_fill, yellow_fill, grey_fill = (PatternFill("solid", fgColor=c) for c in (ACCENT, YELLOW, GREY))
    thin = Border(bottom=Side(style="thin", color="E3E7EC"))
    wrap = Alignment(wrap_text=True, vertical="top")

    def avg(line: str, months: list[str]) -> float:
        if not months:
            return 0.0
        start, end = month_start(months[0]), month_end(months[-1])
        return sum(t.outflow for t in L.expenses_between(start, end) if t.category == line) / len(months)

    by_cat = {s.category: s for s in suggestions}
    if existing.lines:  # your plan, in your order — lines kept together with their group
        order: dict[str, list[str]] = {}
        for line in existing.lines:
            order.setdefault(existing.groups.get(line) or (by_cat[line].group if line in by_cat else
                                                           L.group_of(line)), []).append(line)
        plan = [line for lines_ in order.values() for line in lines_]
    else:
        plan = [s.category for s in suggestions] + [c for c in config_budgets if c not in by_cat]
    in_plan = set(plan)
    spent6: dict[str, float] = {}
    if months6:
        for t in L.expenses_between(month_start(months6[0]), month_end(months6[-1])):
            spent6[t.category] = spent6.get(t.category, 0.0) + t.outflow
    leftovers = sorted((c for c, v in spent6.items() if c not in in_plan and v / max(len(months6), 1) >= 5
                        and L.class_of(c) != CLASS_SAVINGS), key=lambda c: -avg(c, months3))

    def kind(line: str) -> str:
        if line in by_cat and not existing.lines:
            return by_cat[line].kind
        if re.search(r"irregular|sinking", line, re.I):
            return "sinking fund"
        cls = L.class_of(line)
        if cls == CLASS_GIVING or L.is_protected(line):
            return "giving"
        if cls in (CLASS_ESSENTIAL, CLASS_DEBT):
            return "essential"
        return "fees" if cls == CLASS_FEES else "flexible"

    def group(line: str) -> str:
        return existing.groups.get(line) or (by_cat[line].group if line in by_cat else L.group_of(line))

    def budget_value(line: str):
        if line in existing.budgets:
            return existing.budgets[line]
        if line in existing.blank:
            return None
        if line in config_budgets:
            return float(config_budgets[line])
        return by_cat[line].target if line in by_cat else None

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = BUDGET
    period = f"{month_label(months6[0])} – {month_label(months6[-1])}" if months6 else "your data"
    ws["A1"] = "Household budget"
    ws["A1"].font = Font(bold=True, size=16)
    ws["A2"] = ("Edit the yellow cells, then save (keep it an Excel .xlsx file). Every finagent report reads this "
                "file automatically. 'Matches' = words that identify a line's transactions (merchant names like "
                "\"AT&T, Hulu\" or a bank category like \"Groceries\"), separated by commas.")
    ws["A2"].alignment = wrap
    ws.merge_cells("A2:J2")
    ws.row_dimensions[2].height = 46

    first = BUDGET_HEADER_ROW + 1
    last = first + len(plan) + len(leftovers) + 250
    income_rng = f"Income!$D${INCOME_HEADER_ROW + 1}:$D$1000"
    regular_rng = f"Income!$G${INCOME_HEADER_ROW + 1}:$G$1000"
    summary = [
        ("Regular monthly income", f'=SUMIFS({income_rng},{regular_rng},"Yes")', money, False),
        ("Total monthly budget", f"=SUM(C{first}:C{last})", money, False),
        ("Left over each month", "=C4-C5", money_signed, False),
        ("Savings rate", "=IF(C4>0,C6/C4,0)", "0%", False),
        ("Target savings rate", target_rate, "0%", True),
        ("Still to find to hit the target", "=MAX(0,C4*C8-C6)", money, False),
        ("Spending not in your plan (3-mo avg)", f'=SUMIFS(E{first}:E{last},I{first}:I{last},"{NOT_IN_PLAN}")',
         money, False),
        ("Giving goal (% of regular income)", giving_goal, "0%", True),
        ("Giving goal per month", "=C4*C11", money, False),
    ]
    for i, (label, value, fmt, editable) in enumerate(summary):
        r = 4 + i
        ws.cell(r, 1, label).font = bold
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
        c = ws.cell(r, 3, value)
        c.number_format, c.font = fmt, bold
        if editable:
            c.fill = yellow_fill
    ws.cell(BUDGET_HEADER_ROW - 1, 1,
            f"Averages are from {period}. Transfers and card payments are excluded. Spending is filed under the "
            f"line whose Matches it fits; anything that fits no line is listed under '{SEPARATOR}'.").font = \
        Font(italic=True, color="5D6B79")

    for j, h in enumerate(BUDGET_HEADERS, 1):
        c = ws.cell(BUDGET_HEADER_ROW, j, h)
        c.font, c.fill = white_bold, accent_fill
        c.alignment = Alignment(vertical="center", wrap_text=True)

    def write_row(r: int, line: str, budget, matches: str, typ: str, note: str, editable_line: bool) -> None:
        values = [group(line), line, budget, matches, round(avg(line, months3), 2), round(avg(line, months6), 2),
                  round(avg(line, months6[-1:]), 2), f'=IF(C{r}="","",C{r}-E{r})', typ, note]
        for j, v in enumerate(values, 1):
            c = ws.cell(r, j, v)
            c.border = thin
            if j == 3:
                c.number_format = money_cents  # budgets keep their cents ($19.44)
            elif j in (5, 6, 7):
                c.number_format = money
            if j == 8:
                c.number_format = money_signed
        for j in (3, 4, 10):
            ws.cell(r, j).fill = yellow_fill
        if not editable_line:
            for j in (1, 2, 9):
                ws.cell(r, j).fill = grey_fill

    r = first
    for line in plan:
        note = existing.notes.get(line, "")
        write_row(r, line, budget_value(line), ", ".join(existing.matches.get(line, [])), kind(line),
                  OUTDATED_NOTES.get(note, note), True)
        r += 1
    if leftovers:
        r += 1
        ws.cell(r, 1, SEPARATOR).font = Font(bold=True, color=ACCENT)
        ws.cell(r, 4, "Type a budget (or add the name to a line's Matches above) to bring these into your plan.") \
            .font = Font(italic=True, color="5D6B79")
        r += 1
        for line in leftovers:
            write_row(r, line, None, "", NOT_IN_PLAN, "", False)
            r += 1
    for col, width in zip("ABCDEFGHIJ", (24, 28, 15, 32, 13, 13, 12, 15, 12, 40)):
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

    # ---------------------------------------------------------------- Transactions tab
    wt = wb.create_sheet(TXNS)
    wt["A1"] = "Transactions"
    wt["A1"].font = Font(bold=True, size=16)
    wt["A2"] = ("Every expense since " + (month_label(months6[0]) if months6 else "the start") + ", newest first. "
                "Use the filter arrows to show one budget line, merchant or account. To file a transaction "
                "somewhere else, pick a line in 'Move to line' (or 'Exclude' for things that aren't household "
                "spending, like returned influencer purchases) and save — finagent remembers it.")
    wt["A2"].alignment = wrap
    wt.merge_cells("A2:I2")
    wt.row_dimensions[2].height = 46
    for j, h in enumerate(TXN_HEADERS, 1):
        c = wt.cell(TXN_HEADER_ROW, j, h)
        c.font, c.fill = white_bold, accent_fill
    tcol = {h: j for j, h in enumerate(TXN_HEADERS, 1)}
    since = month_start(months6[0]) if months6 else date.min
    # Excluded ones stay listed (so you can see and undo them: clear 'Move to line').
    txns = sorted((t for t in L.all if t.date >= since and (t.kind == KIND_EXPENSE or "excluded by you" in t.flags)),
                  key=lambda t: (t.date, t.outflow), reverse=True)
    grey = Font(color="808080", italic=True)
    for i, t in enumerate(txns):
        r = TXN_HEADER_ROW + 1 + i
        desc = t.memo or t.payee
        excluded = "excluded by you" in t.flags
        row = {"Date": t.date, "Budget line": "Excluded" if excluded else t.category,
               "Merchant": t.merchant or t.payee, "Bank description": desc, "Account": t.account,
               "Amount": round(t.outflow, 2),
               "Bank category": t.orig_category if t.orig_category != t.category else "",
               "Why it's here": why_here(t), "Move to line": existing.overrides.get(t.id, ""),
               "Note": existing.txn_notes.get(t.id, ""), "ID": t.id}
        for j, h in enumerate(TXN_HEADERS, 1):
            c = wt.cell(r, j, row[h])
            if h == "Date":
                c.number_format = "mmm d, yyyy"
            elif h == "Amount":
                c.number_format = money_signed.replace('#,##0', '#,##0.00')
            if excluded and j < tcol["Move to line"]:
                c.font = grey
        wt.cell(r, tcol["Move to line"]).fill = yellow_fill
        wt.cell(r, tcol["Note"]).fill = yellow_fill
    last_txn = TXN_HEADER_ROW + max(len(txns), 1)
    wt.auto_filter.ref = f"A{TXN_HEADER_ROW}:{get_column_letter(len(TXN_HEADERS))}{last_txn}"
    for j, width in enumerate(TXN_WIDTHS, 1):
        wt.column_dimensions[get_column_letter(j)].width = width
    wt.column_dimensions[get_column_letter(tcol["ID"])].hidden = True
    wt.freeze_panes = wt.cell(TXN_HEADER_ROW + 1, 1)
    # A dropdown of your lines (+ Exclude) for "Move to line"; other text is allowed after a warning.
    wl = wb.create_sheet(LISTS)
    choices = list(plan) + [c for c in leftovers if c not in plan] + ["Exclude"]
    for i, name in enumerate(choices, 1):
        wl.cell(i, 1, name)
    wl.sheet_state = "hidden"
    move_dv = DataValidation(type="list", formula1=f"={LISTS}!$A$1:$A${len(choices)}", allow_blank=True,
                             showErrorMessage=True, errorStyle="warning",
                             error="That isn't one of your budget lines. Keep it anyway?")
    wt.add_data_validation(move_dv)
    if txns:
        move_col = get_column_letter(tcol["Move to line"])
        move_dv.add(f"{move_col}{TXN_HEADER_ROW + 1}:{move_col}{last_txn}")

    # ---------------------------------------------------------------- How to use
    wh = wb.create_sheet(HELP)
    lines = [
        ("How to use this budget", True),
        ("1. Income tab: mark each source Yes (regular, like your paychecks) or No (one-time money).", False),
        ("2. Budget tab: one row per budget line. Type the monthly amount in 'Monthly budget'.", False),
        ("3. 'Matches': words that identify the line's transactions, separated by commas — merchant names", False),
        ("   (AT&T, Hulu, Kroger) or a bank category (Groceries, Dining Out). Every matching transaction is", False),
        ("   filed under that line automatically. Leave it empty if the line's name is already the category.", False),
        (f"4. '{SEPARATOR}': spending that matched none of your lines. Type a budget next to one to make it a", False),
        ("   line, or add its name to the Matches of an existing line.", False),
        ("5. Add a line: type it in a new row under your other lines (Group, Budget line, budget, Matches).", False),
        ("6. Save as Excel (.xlsx). In Numbers, use File → Export To → Excel and replace this file.", False),
        ("7. Transactions tab: every expense with the line it landed on. Filter to review a line; use", False),
        ("   'Move to line' to file one somewhere else, or 'Exclude' for things that aren't household spending.", False),
        ("8. Run any finagent report — it reads this file. Run `finagent sheet` (with this file closed) to", False),
        ("   refresh the averages; your lines, budgets, matches, notes and Yes/No choices are kept.", False),
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
    """Change budget cells in the workbook (0 clears one). A line that isn't listed is added at the end."""
    openpyxl = require_openpyxl()
    from openpyxl.styles import PatternFill
    wb = openpyxl.load_workbook(path)
    ws = wb[BUDGET]
    hr = _find_header(ws, ("Monthly budget",), BUDGET_HEADER_ROW, ("Budget line", "Category"))
    col = _header_index(ws, hr)
    c_line, c_bud = col.get("Budget line") or col.get("Category"), col["Monthly budget"]
    report = []
    for name, amount in changes.items():
        row = next((r for r in range(hr + 1, ws.max_row + 1)
                    if str(ws.cell(r, c_line).value or "").strip().lower() == name.lower()), None)
        if row is None:
            row = max((r for r in range(hr + 1, ws.max_row + 1) if ws.cell(r, c_line).value), default=hr) + 1
            ws.cell(row, c_line, name)
            if "Group" in col:
                ws.cell(row, col["Group"], "Other")
            ws.cell(row, c_bud).number_format = ws.cell(hr + 1, c_bud).number_format
            ws.cell(row, c_bud).fill = PatternFill("solid", fgColor=YELLOW)
            old = None
        else:
            old = _number(ws.cell(row, c_bud).value)
        ws.cell(row, c_bud, amount if amount > 0 else None)
        if "Type" in col and str(ws.cell(row, col["Type"]).value or "").lower() == NOT_IN_PLAN and amount > 0:
            ws.cell(row, col["Type"], "flexible")  # it's part of the plan now
        report.append((str(ws.cell(row, c_line).value), old, amount))
    wb.save(path)
    return report


def set_matches(path: Path, line: str, terms: list[str], mode: str = "add") -> tuple[str, list[str]]:
    """Add (default), replace, or remove Matches terms on a budget line. Returns (line, new terms)."""
    openpyxl = require_openpyxl()
    wb = openpyxl.load_workbook(path)
    ws = wb[BUDGET]
    hr = _find_header(ws, ("Monthly budget",), BUDGET_HEADER_ROW, ("Budget line", "Category"))
    col = _header_index(ws, hr)
    if "Matches" not in col:
        raise ValueError("This spreadsheet has no Matches column — run `finagent sheet` to update it first.")
    c_line = col.get("Budget line") or col.get("Category")
    row = next((r for r in range(hr + 1, ws.max_row + 1)
                if str(ws.cell(r, c_line).value or "").strip().lower() == line.lower()), None)
    if row is None:
        raise ValueError(f'There\'s no budget line called "{line}" on the Budget tab.')
    current = split_terms(ws.cell(row, col["Matches"]).value)
    if mode == "replace":
        new = list(terms)
    elif mode == "remove":
        drop = {t.lower() for t in terms}
        new = [t for t in current if t.lower() not in drop]
    else:
        new = current + [t for t in terms if t.lower() not in {c.lower() for c in current}]
    ws.cell(row, col["Matches"], ", ".join(new) if new else None)
    if "Type" in col and str(ws.cell(row, col["Type"]).value or "").lower() == NOT_IN_PLAN and new:
        ws.cell(row, col["Type"], "flexible")
    wb.save(path)
    return str(ws.cell(row, c_line).value), new
