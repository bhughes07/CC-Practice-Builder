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


def test_sheet_create_edit_refresh_roundtrip(tmp_path, capsys):
    base = _setup(tmp_path)
    path = tmp_path / "budget.xlsx"
    assert main(base + ["sheet", "--no-open"]) == 0
    assert path.exists() and "Created" in capsys.readouterr().out

    wb = openpyxl.load_workbook(path)
    ws, wi = wb["Budget"], wb["Income"]
    assert ws["C4"].value.startswith("=SUMIFS(Income!") and ws["C5"].value.startswith("=SUM(C")
    assert ws.cell(11, 3).value == "Monthly budget"
    income = {wi.cell(r, 1).value: wi.cell(r, 7).value for r in range(6, wi.max_row + 1) if wi.cell(r, 1).value}
    assert income == {"Acme Engineering Payroll": "Yes", "Lakeside Schools Payroll": "Yes",
                      "IRS Treas 310 Tax Ref": "No"}
    assert wi.data_validations.dataValidation[0].formula1 == '"Yes,No"'

    # --- you edit it in Excel: change a budget, add a note, add your own row, flip an income source
    rows = {ws.cell(r, 2).value: r for r in range(12, ws.max_row + 1) if ws.cell(r, 2).value}
    ws.cell(rows["Dining Out"], 3).value = 650
    ws.cell(rows["Dining Out"], 9).value = "2 dinners out a week"
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
    rows = {ws.cell(r, 2).value: r for r in range(12, ws.max_row + 1) if ws.cell(r, 2).value}
    assert ws.cell(rows["Dining Out"], 3).value == 650
    assert ws.cell(rows["Dining Out"], 9).value == "2 dinners out a week"
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
    rows = {ws.cell(r, 2).value: r for r in range(12, ws.max_row + 1) if ws.cell(r, 2).value}
    assert ws.cell(rows["Dining Out"], 3).value == 700 and ws.cell(rows["Gas & Fuel"], 3).value == 280

    # --- reports use the sheet's budgets and income choices
    assert main(base + ["review", "--months", "3", "--out", str(tmp_path / "r")]) == 0
    html = next((tmp_path / "r").glob("review-*.html")).read_text()
    assert "Not counted as regular income" in html and "Lakeside Schools Payroll" in html


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
