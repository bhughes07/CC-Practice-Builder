from datetime import date

import pytest

from finagent.categorize import category_class, enrich, normalize_merchant
from finagent.util import (
    add_months, complete_months, fmt_range, infer_date_order, money_auto, parse_amount, parse_date,
)
from tests.conftest import tx


@pytest.mark.parametrize("raw,expected", [
    ("$1,234.56", 1234.56), ("(12.34)", -12.34), ("-$5", -5.0), ("12.34-", -12.34), ("−7.00", -7.0),
    ("", None), ("abc", None), (42, 42.0), ("1,000", 1000.0), ("25.00 DR", -25.0),
])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


def test_parse_date_orders():
    assert parse_date("2026-03-04") == date(2026, 3, 4)
    assert parse_date("03/04/2026") == date(2026, 3, 4)
    assert parse_date("03/04/2026", "dmy") == date(2026, 4, 3)
    assert parse_date("Mar 4, 2026") == date(2026, 3, 4)
    assert infer_date_order(["03/04/2026", "25/04/2026"]) == "dmy"
    assert infer_date_order(["03/04/2026", "04/25/2026"]) == "mdy"


def test_month_helpers():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert complete_months(date(2026, 11, 9), 3) == ["2026-08", "2026-09", "2026-10"]
    assert complete_months(date(2026, 1, 15), 2) == ["2025-11", "2025-12"]
    assert fmt_range(date(2026, 11, 2), date(2026, 11, 8)) == "Nov 2 – 8, 2026"
    assert money_auto(60) == "$60" and money_auto(17.99) == "$17.99" and money_auto(1225.6) == "$1,226"


@pytest.mark.parametrize("raw,expected", [
    ("AMZN Mktp US*2K4L12345", "Amazon"),
    ("Amazon Prime*2K41L", "Amazon Prime"),
    ("UBER *EATS PENDING", "Uber Eats"),
    ("UBER *TRIP HELP.UBER.COM", "Uber"),
    ("SQ *LOCAL TACO CO", "Local Taco"),
    ("TST* FIREBIRD GRILL", "Firebird Grill"),
    ("CINEMARK THEATRES 412", "Cinemark Theatres"),
    ("NETFLIX.COM", "Netflix"),
    ("APPLE.COM/BILL", "Apple"),
    ("CHECKCARD 1105 BLUE BOTTLE COFFEE #12 DALLAS TX", "Blue Bottle Coffee Dallas"),
    ("Online Giving - Church", "Online Giving - Church"),
    ("CITY OF LAKESIDE UTILITIES", "City of Lakeside Utilities"),
])
def test_normalize_merchant(raw, expected):
    assert normalize_merchant(raw) == expected


@pytest.mark.parametrize("cat,group,cls", [
    ("Tithe", "Giving", "giving"), ("Dining Out", "Food", "discretionary"), ("Groceries", "Food", "essential"),
    ("Mortgage", "Housing", "essential"), ("Car Payment", "Transportation", "debt"),
    ("Emergency Fund", "Savings", "savings"), ("Bank Fees", "Financial", "fees"),
    ("Paycheck", "Income", "income"), ("Inflow: Ready to Assign", "Internal Master Category", "income"),
    ("Coffee Shops", "Food", "discretionary"), ("Kids Activities", "Family", "discretionary"),
])
def test_category_class(cat, group, cls):
    assert category_class(cat, group) == cls


def test_kinds_transfers_income_savings(cfg):
    rows = enrich([
        tx("2026-10-01", 2450, "ACME PAYROLL"),                     # uncategorized payroll → income
        tx("2026-10-02", -300, "Transfer to Savings", is_transfer=True),
        tx("2026-10-03", -2800, "CHASE CREDIT CRD AUTOPAY"),        # card payment → transfer
        tx("2026-10-04", -200, "Vanguard", "Retirement", "Savings"),  # saving isn't spending
        tx("2026-10-05", -50, "KROGER #512"),                       # guessed Groceries → expense
        tx("2026-10-06", 45.99, "AMZN Mktp US Refund", "Shopping"),  # refund stays an expense (negative spend)
    ], cfg)
    kinds = {t.payee: (t.kind, t.category) for t in rows}
    assert kinds["ACME PAYROLL"][0] == "income"
    assert kinds["Transfer to Savings"][0] == "transfer"
    assert kinds["CHASE CREDIT CRD AUTOPAY"][0] == "transfer"
    assert kinds["Vanguard"][0] == "transfer"
    assert kinds["KROGER #512"] == ("expense", "Groceries")
    assert kinds["AMZN Mktp US Refund"][0] == "expense"


def test_merchant_rules_and_ignore():
    cfg = {"categories": {"merchant_rules": {"Venmo": "Kids Activities"}, "ignore": ["Reimbursable"]}}
    rows = enrich([tx("2026-10-01", -40, "VENMO *COACH"), tx("2026-10-02", -99, "Hotel", "Reimbursable")], cfg)
    assert len(rows) == 1 and rows[0].category == "Kids Activities"


@pytest.mark.parametrize("cat,cls", [("Food & Drink", "discretionary"), ("Software & Tech", "discretionary"),
                                     ("Bills & Utilities", "essential"), ("Gifts & Donations", "giving"),
                                     ("Restaurants & Bars", "discretionary"), ("Internal Transfers", "transfer")])
def test_common_app_categories(cat, cls):
    assert category_class(cat) == cls


def test_budget_line_matching_rules():
    rules = [{"line": "iCloud", "group": "Subs", "terms": ["Apple $2.99"]},
             {"line": "Apple", "group": "Subs", "terms": ["Apple"]},
             {"line": "Food Out", "group": "Food", "terms": ["Dining Out", "Starbucks"]},
             {"line": "Power", "group": "House", "terms": ["Gas & Electric"]}]
    cfg = {"categories": {"line_rules": rules}}
    rows = enrich([
        tx("2026-10-02", -2.99, "APPLE.COM/BILL", "Subscriptions", n=1),
        tx("2026-10-02", -10.81, "APPLE.COM/BILL", "Subscriptions", n=2),
        tx("2026-10-03", -42.0, "APPLEBEES 123", "Dining Out", n=3),       # not "Apple": word boundary
        tx("2026-10-04", -6.5, "STARBUCKS 99", "Coffee Shops", n=4),        # merchant term
        tx("2026-10-05", -180.0, "TXU ENERGY", "Gas & Electric", "Utilities", n=5),  # category term
        tx("2026-10-06", -900.0, "CARD PAYMENT", "", is_transfer=True, n=6),
        tx("2026-10-07", 2450.0, "ACME PAYROLL", "Paycheck", "Income", n=7),
    ], cfg)
    got = {t.payee: (t.category, t.category_group) for t in rows}
    assert got["APPLE.COM/BILL"] in {("iCloud", "Subs"), ("Apple", "Subs")}
    assert sorted(t.category for t in rows if t.payee == "APPLE.COM/BILL") == ["Apple", "iCloud"]
    assert got["APPLEBEES 123"] == ("Food Out", "Food") and got["STARBUCKS 99"] == ("Food Out", "Food")
    assert got["TXU ENERGY"] == ("Power", "House")
    assert got["CARD PAYMENT"][0] == "" and got["ACME PAYROLL"][0] == "Paycheck"  # transfers & income untouched
