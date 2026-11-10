from datetime import date

import pytest

from finagent.cli import main
from finagent.config import EXAMPLE_CONFIG, load_config
from finagent.ledger import Ledger
from finagent.sources.demo import generate
from finagent.store import Store
from tests.conftest import TODAY, tx

openpyxl = pytest.importorskip("openpyxl")


def test_one_time_income_never_counts_as_regular(cfg):
    txns, _ = generate(TODAY)
    txns += [tx("2026-07-03", 15000.0, "SMITH LAW GROUP SETTLEMENT", "Other Income", "Income", n=1),
             tx("2026-09-01", 300.0, "Grandma", "Other Income", "Income", n=2)]
    L = Ledger(txns, [], cfg, TODAY)
    one_time = {t.payee for t in L.one_time_incomes}
    assert {"IRS TREAS 310 TAX REF", "SMITH LAW GROUP SETTLEMENT", "Grandma"} <= one_time
    assert all("Payroll" in t.merchant for t in L.incomes)
    july = L.income_month("2026-07")
    assert july == pytest.approx(2450 * 3 + 1650 * 2)  # three Acme paychecks + two school paychecks only
    assert "one-time income" in next(t for t in L.one_time_incomes if "LAW" in t.payee).flags


def test_income_rules_from_config():
    txns, _ = generate(TODAY)
    only_acme = load_config(overrides={"income": {"regular": ["ACME"]}})
    L = Ledger(txns, [], only_acme, TODAY)
    assert {t.merchant for t in L.incomes} == {"Acme Engineering Payroll"}
    by_sheet = load_config(overrides={"income": {"sources": {"Lakeside Schools Payroll": False,
                                                             "IRS Treas 310 Tax Ref": True}}})
    L2 = Ledger(txns, [], by_sheet, TODAY)
    assert "Lakeside Schools Payroll" not in {t.merchant for t in L2.incomes}
    assert "IRS Treas 310 Tax Ref" in {t.merchant for t in L2.incomes}


def _setup(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(EXAMPLE_CONFIG.read_text())
    txns, _ = generate(TODAY)
    with Store(tmp_path / "data" / "finance.db") as store:
        store.upsert_transactions(txns)
    return ["--config", str(cfg), "--today", TODAY.isoformat()]


def _cols(ws):
    hr = next(r for r in range(1, 30) if ws.cell(r, 3).value == "Monthly budget")
    col = {ws.cell(hr, c).value: c for c in range(1, 15) if ws.cell(hr, c).value}
    rows = {ws.cell(r, col["Budget line"]).value: r for r in range(hr + 1, ws.max_row + 1)
            if ws.cell(r, col["Budget line"]).value}
    return hr, col, rows


def test_sheet_create_edit_refresh_roundtrip(tmp_path, capsys):
    base = _setup(tmp_path)
    path = tmp_path / "budget.xlsx"
    assert main(base + ["sheet", "--no-open"]) == 0
    assert path.exists() and "Created" in capsys.readouterr().out

    wb = openpyxl.load_workbook(path)
    ws, wi = wb["Budget"], wb["Income"]
    assert ws["C4"].value.startswith("=SUMIFS(Income!") and ws["C5"].value.startswith("=SUM(C")
    assert ws["A10"].value.startswith("Spending not in your plan")
    hr, col, rows = _cols(ws)
    assert list(col)[:4] == ["Group", "Budget line", "Monthly budget", "Matches"]
    income = {wi.cell(r, 1).value: wi.cell(r, 7).value for r in range(6, wi.max_row + 1) if wi.cell(r, 1).value}
    assert income == {"Acme Engineering Payroll": "Yes", "Lakeside Schools Payroll": "Yes",
                      "IRS Treas 310 Tax Ref": "No"}
    assert wi.data_validations.dataValidation[0].formula1 == '"Yes,No"'

    # --- you edit it in Excel: change a budget, add a note, add your own row, flip an income source
    ws.cell(rows["Dining Out"], 3).value = 650
    ws.cell(rows["Dining Out"], col["Notes"]).value = "2 dinners out a week"
    ws.cell(rows["Coffee Shops"], 3).value = None  # no budget for this one
    new_row = max(rows.values()) + 1
    ws.cell(new_row, 1).value, ws.cell(new_row, 2).value, ws.cell(new_row, 3).value = "Fun", "Date Night", 120
    lakeside = next(r for r in range(6, wi.max_row + 1) if wi.cell(r, 1).value == "Lakeside Schools Payroll")
    wi.cell(lakeside, 7).value = "No"
    wb.save(path)

    # --- finagent picks it up everywhere
    assert main(base + ["budgets"]) == 0
    out = capsys.readouterr().out
    assert "budget.xlsx" in out and "Dining Out" in out and "$650" in out and "Date Night" in out
    assert "Coffee Shops" not in out
    assert "Average income: $4,900/mo" in out  # only Acme counts now: 6 paychecks × $2,450 over Aug–Oct

    # --- refreshing keeps everything you typed
    assert main(base + ["sheet", "--no-open"]) == 0
    assert "kept" in capsys.readouterr().out and (tmp_path / "budget.backup.xlsx").exists()
    wb = openpyxl.load_workbook(path)
    ws, wi = wb["Budget"], wb["Income"]
    hr, col, rows = _cols(ws)
    assert ws.cell(rows["Dining Out"], 3).value == 650
    assert ws.cell(rows["Dining Out"], col["Notes"]).value == "2 dinners out a week"
    assert ws.cell(rows["Coffee Shops"], 3).value is None and ws.cell(rows["Date Night"], 3).value == 120
    assert "No" == next(wi.cell(r, 7).value for r in range(6, wi.max_row + 1)
                        if wi.cell(r, 1).value == "Lakeside Schools Payroll")

    # --- budget-set writes into the spreadsheet once it exists (config.toml untouched)
    before = (tmp_path / "config.toml").read_text()
    assert main(base + ["budget-set", "Dining Out=700", "Gas & Fuel=280"]) == 0
    out = capsys.readouterr().out
    assert "Dining Out: $650 → $700" in out and "budget.xlsx" in out
    assert (tmp_path / "config.toml").read_text() == before
    ws = openpyxl.load_workbook(path)["Budget"]
    hr, col, rows = _cols(ws)
    assert ws.cell(rows["Dining Out"], 3).value == 700 and ws.cell(rows["Gas & Fuel"], 3).value == 280

    # --- reports use the sheet's budgets and income choices
    assert main(base + ["review", "--months", "3", "--out", str(tmp_path / "r")]) == 0
    html = next((tmp_path / "r").glob("review-*.html")).read_text()
    assert "Not counted as regular income" in html and "Lakeside Schools Payroll" in html


def test_your_own_budget_lines_with_matches(tmp_path, capsys):
    """Import a hand-made plan (like one copied from another budgeting app): lines + Matches."""
    base = _setup(tmp_path)
    starter = tmp_path / "starter.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Budget"
    ws.append(["Group", "Budget line", "Monthly budget", "Matches", "Notes"])
    for row in [("Giving", "Church Tithe", 800, "Online Giving - Church", ""),
                ("Housing", "Mortgage", 2150, "", ""),
                ("Housing", "Power Bill", 250, "TXU Energy, Electric", ""),
                ("Subscriptions", "Streaming", 40, "Netflix, Hulu, Disney+", ""),
                ("Subscriptions", "iCloud", 2.99, "Apple $2.99", ""),
                ("Subscriptions", "Apps", 9.99, "Apple", ""),
                ("Lifestyle", "Birthdays", None, "", "plan ahead")]:
        ws.append(list(row))
    wb.save(starter)

    assert main(base + ["sheet", "--no-open", "--import", str(starter)]) == 0
    assert "Importing 7 budget lines" in capsys.readouterr().out
    ws = openpyxl.load_workbook(tmp_path / "budget.xlsx")["Budget"]
    hr, col, rows = _cols(ws)
    order = [ws.cell(r, col["Budget line"]).value for r in range(hr + 1, hr + 8)]
    assert order == ["Church Tithe", "Mortgage", "Power Bill", "Streaming", "iCloud", "Apps", "Birthdays"]
    avg = lambda line: ws.cell(rows[line], col["Avg last 3 mo"]).value  # noqa: E731
    assert avg("Church Tithe") == 800 and avg("Mortgage") == 2150
    assert avg("iCloud") == 2.99 and avg("Apps") == 9.99  # same merchant, told apart by amount
    assert 40 < avg("Streaming") < 42  # Netflix ~$17 + Hulu $9.99 + Disney+ $13.99 (Max isn't listed)
    assert avg("Power Bill") > 100
    sep = next(r for r in range(hr, ws.max_row + 1) if ws.cell(r, 1).value == "Not in your plan yet")
    leftovers = {ws.cell(r, 2).value for r in range(sep + 1, ws.max_row + 1) if ws.cell(r, 2).value}
    assert {"Dining Out", "Groceries", "Shopping"} <= leftovers
    assert "Mortgage" not in leftovers and "Electric" not in leftovers  # re-filed under Power Bill
    assert all(ws.cell(r, col["Type"]).value == "not in plan" for r in range(sep + 1, ws.max_row + 1)
               if ws.cell(r, 2).value)

    # leftovers are informational: refreshing doesn't turn them into lines...
    assert main(base + ["sheet", "--no-open"]) == 0
    capsys.readouterr()
    assert "Dining Out" not in load_sheet_lines(tmp_path / "budget.xlsx")
    # ...until you give one a budget
    assert main(base + ["budget-set", "Dining Out=900"]) == 0
    capsys.readouterr()
    assert main(base + ["sheet", "--no-open"]) == 0
    assert "Dining Out" in load_sheet_lines(tmp_path / "budget.xlsx")

    # reports and budgets use your lines
    assert main(base + ["budgets"]) == 0
    out = capsys.readouterr().out
    assert "Power Bill" in out and "Church Tithe" in out and "Electric " not in out


def load_sheet_lines(path):
    from finagent.sheet import read_sheet
    return read_sheet(path).lines


def test_sheet_without_openpyxl_falls_back_with_a_clear_message(tmp_path, monkeypatch, capsys):
    base = _setup(tmp_path)
    assert main(base + ["sheet", "--no-open"]) == 0
    capsys.readouterr()
    from finagent import sheet

    def missing():
        raise sheet.SheetUnavailable("needs openpyxl: python3 -m pip install openpyxl")
    monkeypatch.setattr(sheet, "require_openpyxl", missing)
    assert main(base + ["budgets"]) == 0
    captured = capsys.readouterr()
    assert "pip install openpyxl" in captured.err and "config.toml" in captured.err
    assert main(base + ["sheet", "--no-open"]) == 2


def test_review_lists_one_time_money(demo):
    ledger, A = demo
    from finagent.reports.builders import build_review
    from finagent.reports.render_html import render_html
    html = render_html(build_review(ledger, A))
    assert "Not counted as regular income: $2,140" in html and "IRS Treas 310 Tax Ref" in html
    assert A.to_summary()["one_time_income_not_counted"][0]["amount"] == 2140.0
    assert date(2026, 7, 14) in {t.date for t in ledger.one_time_incomes}
