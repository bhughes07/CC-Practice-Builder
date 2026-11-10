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


def test_giving_goal_comes_first_with_a_funding_plan():
    from finagent.insights import Analysis
    txns, budgets = generate(TODAY)
    no_giving = [t for t in txns if "Giving" not in t.payee]  # tithe currently 0
    cfg = load_config(overrides={"analysis": {"giving_goal": 0.10}})
    L = Ledger(no_giving, budgets, cfg, TODAY)
    A = Analysis(L, L.available_months(6))
    first = A.insights[0]
    assert first.key == "giving-goal" and first.kind == "goal" and "Restore giving to 10%" in first.title
    assert A.giving_target == round(A.avg_income * 0.10, 2) == 860.83  # 10% of 6-month regular income
    assert A.giving_recent == 0 and A.giving_gap == A.giving_target
    assert "Dining Out" in first.action and "starting next month" in first.action  # the cuts cover it
    assert "restore giving to 10% ($861/mo)" in A.headline()
    assert A.to_summary()["giving_goal"]["gap_per_month"] == 860.83

    # Giving at the goal ($850/mo vs 9% = $775) is a win instead
    L2 = Ledger(txns, budgets, load_config(overrides={"analysis": {"giving_goal": 0.09}}), TODAY)
    A2 = Analysis(L2, L2.available_months(6))
    assert all(i.key != "giving-goal" for i in A2.insights)
    assert A2.wins[0].title == "Giving is at your 9% goal"


def test_giving_goal_set_from_the_sheet(tmp_path, capsys):
    base = _setup(tmp_path)
    assert main(base + ["sheet", "--no-open", "--giving-goal", "10"]) == 0
    assert "Giving goal: 10% of regular income = $861/mo (lately $850/mo)" in capsys.readouterr().out
    ws = openpyxl.load_workbook(tmp_path / "budget.xlsx")["Budget"]
    assert ws["A11"].value.startswith("Giving goal (%") and ws["C11"].value == 0.1
    assert ws["C12"].value == "=C4*C11"
    assert main(base + ["sheet", "--no-open"]) == 0  # refresh keeps the goal
    capsys.readouterr()
    assert openpyxl.load_workbook(tmp_path / "budget.xlsx")["Budget"]["C11"].value == 0.1
    assert main(base + ["review", "--out", str(tmp_path / "r")]) == 0
    html = next((tmp_path / "r").glob("review-*.html")).read_text()
    assert "goal 10% = $861/mo" in html  # the Giving tile


def test_bank_ach_descriptors_group_into_one_source(cfg):
    from finagent.categorize import normalize_merchant as n
    a = n("Acme Corp Des:payroll Id:xxxxxxxxxx0039q Indn:doe,jane Q Co Id:xxxxx11101 Ppd")
    b = n("ACME CORP DES:PAYROLL ID:XXXXXXXXXX7739Q INDN:DOE,JANE Q CO ID:XXXXX11101 PPD")
    c = n("ACME CORP DES:PAYROLL ID:XXXXXXXXXX1939Q INDN:DOE,JOHN C CO ID:XXXXX11101 PPD")
    assert a == b == "Acme Corp Payroll (Jane)" and c == "Acme Corp Payroll (John)"
    assert n("IRS TREAS 310 DES:TAX REF ID:XXXXX00918 INDN:DOE, JANE & JOHN CO ID:XXX36170 PPD") == "IRS Treas 310 Tax Ref"
    assert n("LAKEVIEW LOAN SRV DES:MTG PYMT ID:000012345 INDN:DOE JANE CO ID:XXXX PPD") == "Lakeview Loan Srv Mtg Pymt"
    pays = [tx(date(2026, m, d), 2500.0, f"ACME CORP DES:PAYROLL ID:XX{m}{d}Q INDN:DOE,JANE CO ID:XX1 PPD",
               "Paycheck", "Income", n=m * 100 + d) for m in range(5, 11) for d in (1, 15)]
    L = Ledger(pays, [], cfg, TODAY)
    sources = L.income_sources(date(2026, 5, 1), date(2026, 10, 31))
    assert [(s["source"], s["count"]) for s in sources] == [("Acme Corp Payroll (Jane)", 12)]


def test_zero_budgets_grouping_notes_and_matches(tmp_path, capsys):
    base = _setup(tmp_path)
    assert main(base + ["sheet", "--no-open"]) == 0
    capsys.readouterr()
    path = tmp_path / "budget.xlsx"
    wb = openpyxl.load_workbook(path)
    ws = wb["Budget"]
    hr, col, rows = _cols(ws)
    # Make "Coffee Shops" an explicit $0 plan, and give a line an old-style note
    ws.cell(rows["Coffee Shops"], 3).value = 0
    ws.cell(rows["Mortgage"], col["Notes"]).value = (
        "Card payments are transfers in finagent: purchases on the card are already counted line by line. "
        "Keep a budget here only for paying down an old balance.")
    wb.save(path)
    assert main(base + ["sheet", "--no-open"]) == 0
    capsys.readouterr()
    ws = openpyxl.load_workbook(path)["Budget"]
    hr, col, rows = _cols(ws)
    assert ws.cell(rows["Coffee Shops"], 3).value == 0  # a typed $0 survives refresh
    assert ws.cell(rows["Mortgage"], col["Notes"]).value.startswith("Card payments from checking land here")
    groups = [ws.cell(r, 1).value for r in range(hr + 1, ws.max_row + 1) if ws.cell(r, 2).value
              and ws.cell(r, col["Type"]).value != "not in plan"]
    assert groups == sorted(groups, key=lambda g: groups.index(g))  # each group's lines are together

    assert main(base + ["budget-match", "Gas & Fuel", "QuikTrip", "Shell"]) == 0
    assert "Gas & Fuel now matches: QuikTrip, Shell" in capsys.readouterr().out
    assert main(base + ["budget-match", "Gas & Fuel", "Shell", "--remove"]) == 0
    assert "now matches: QuikTrip" in capsys.readouterr().out
    assert main(base + ["budget-match", "No Such Line", "X"]) == 2


def test_transactions_tab_move_and_exclude(tmp_path, capsys):
    base = _setup(tmp_path)
    assert main(base + ["sheet", "--no-open"]) == 0
    capsys.readouterr()
    path = tmp_path / "budget.xlsx"
    wb = openpyxl.load_workbook(path)
    assert "Transactions" in wb.sheetnames and wb["Lists"].sheet_state == "hidden"
    wt = wb["Transactions"]
    hdr = {wt.cell(4, c).value: c for c in range(1, wt.max_column + 1)}
    assert list(hdr) == ["Date", "Budget line", "Merchant", "Bank description", "Account", "Amount",
                         "Bank category", "Why it's here", "Move to line", "Note", "ID"]
    assert wt.auto_filter.ref.startswith("A4:K") and wt.column_dimensions["K"].hidden
    rows = list(range(5, wt.max_row + 1))
    dates = [wt.cell(r, 1).value for r in rows]
    assert dates == sorted(dates, reverse=True)  # newest first
    best_buy = [r for r in rows if wt.cell(r, hdr["Merchant"]).value == "Best Buy"]
    steakhouse = next(r for r in rows if wt.cell(r, hdr["Merchant"]).value == "Lakeside Steakhouse")
    assert len(best_buy) == 2
    wt.cell(best_buy[0], hdr["Move to line"]).value = "Exclude"          # the duplicate we got refunded
    wt.cell(steakhouse, hdr["Move to line"]).value = "Gifts"              # it was a birthday dinner
    wt.cell(steakhouse, hdr["Note"]).value = "Mom's birthday"
    wb.save(path)

    assert main(base + ["transactions", "Best Buy"]) == 0
    out = capsys.readouterr().out
    assert "1 transactions · $89.99" in out  # the excluded one is gone from spending
    assert main(base + ["transactions", "Steakhouse"]) == 0
    assert "Gifts" in capsys.readouterr().out

    assert main(base + ["sheet", "--no-open"]) == 0  # choices survive a refresh
    capsys.readouterr()
    wt = openpyxl.load_workbook(path)["Transactions"]
    moved = {wt.cell(r, hdr["Merchant"]).value: (wt.cell(r, hdr["Budget line"]).value,
                                                wt.cell(r, hdr["Move to line"]).value,
                                                wt.cell(r, hdr["Bank category"]).value, wt.cell(r, hdr["Note"]).value)
             for r in range(5, wt.max_row + 1) if wt.cell(r, hdr["Move to line"]).value}
    assert moved["Lakeside Steakhouse"] == ("Gifts", "Gifts", "Dining Out", "Mom's birthday")
    assert moved["Best Buy"][1] == "Exclude"


def test_ignored_accounts_stay_out_of_the_budget(cfg):
    from finagent.categorize import enrich
    rows = enrich([tx("2026-10-01", -45.0, "AMZN Mktp US*1", "Shopping", account="Influencer Card ••9", n=1),
                   tx("2026-10-01", -45.0, "KROGER", "Groceries", account="Checking", n=2)],
                  {"categories": {"ignore_accounts": ["influencer card"]}})
    assert [t.payee for t in rows] == ["KROGER"]


def test_excluded_transactions_stay_listed_and_can_be_undone(tmp_path, capsys):
    base = _setup(tmp_path)
    assert main(base + ["sheet", "--no-open"]) == 0
    path = tmp_path / "budget.xlsx"

    def best_buy_rows():
        wt = openpyxl.load_workbook(path)["Transactions"]
        col = {wt.cell(4, c).value: c for c in range(1, wt.max_column + 1)}
        return wt, col, [r for r in range(5, wt.max_row + 1) if wt.cell(r, col["Merchant"]).value == "Best Buy"]

    wt, col, rows = best_buy_rows()
    wt.cell(rows[0], col["Move to line"]).value = "Exclude"
    wt.parent.save(path)
    assert main(base + ["sheet", "--no-open"]) == 0
    wt, col, rows = best_buy_rows()
    assert len(rows) == 2 and sorted(wt.cell(r, col["Budget line"]).value for r in rows)[0] == "Excluded"
    excluded = next(r for r in rows if wt.cell(r, col["Budget line"]).value == "Excluded")
    assert wt.cell(excluded, col["Why it's here"]).value == "You excluded it"
    wt.cell(excluded, col["Move to line"]).value = None  # changed my mind
    wt.parent.save(path)
    capsys.readouterr()
    assert main(base + ["transactions", "Best Buy"]) == 0
    assert "2 transactions" in capsys.readouterr().out
