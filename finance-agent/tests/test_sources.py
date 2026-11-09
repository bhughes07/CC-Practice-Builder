from datetime import date

from finagent.sources.csv_import import detect_format, parse_csv_text
from finagent.sources.ynab import YNABSource
from finagent.store import Store

YNAB = '''"Account","Flag","Date","Payee","Category Group/Category","Category Group","Category","Memo","Outflow","Inflow","Cleared"
"Checking","","10/01/2026","Kroger","Food: Groceries","Food","Groceries","","$84.12","$0.00","Cleared"
"Checking","","10/01/2026","Employer","Inflow: Ready to Assign","Inflow","Ready to Assign","","$0.00","$2,450.00","Cleared"
"Checking","","10/02/2026","Transfer : Savings","","","","","$300.00","$0.00","Cleared"
"Checking","","10/03/2026","Transfer : Mortgage","Housing: Mortgage","Housing","Mortgage","","$2,150.00","$0.00","Cleared"
'''

MONARCH = '''Date,Merchant,Category,Account,Original Statement,Notes,Amount,Tags
2026-10-01,Kroger,Groceries,Visa,KROGER #512,,-84.12,
2026-10-02,Employer,Paychecks,Checking,ACME PAYROLL,,2450.00,
2026-10-03,Chase,Credit Card Payment,Checking,CHASE AUTOPAY,,-900.00,
'''

MINT = '''"Date","Description","Original Description","Amount","Transaction Type","Category","Account Name","Labels","Notes"
"10/01/2026","Kroger","KROGER #512","84.12","debit","Groceries","Visa","",""
"10/02/2026","Acme","ACME PAYROLL","2450.00","credit","Paycheck","Checking","",""
'''

ROCKET = '''Date,Original Date,Account Type,Account Name,Account Number,Institution Name,Name,Custom Name,Amount,Description,Category,Note,Ignored From,Tax Deductible
2026-10-01,2026-10-01,Credit Card,Visa,1234,Chase,KROGER #512,,84.12,KROGER,Groceries,,,
2026-10-02,2026-10-02,Credit Card,Visa,1234,Chase,NETFLIX.COM,Netflix,17.99,NETFLIX,Entertainment,,,
2026-10-03,2026-10-03,Checking,Checking,9876,Chase,ACME PAYROLL,,-2450.00,PAYROLL,Income,,,
2026-10-04,2026-10-04,Checking,Checking,9876,Chase,CHASE CARD,,900.00,PAYMENT,Credit Card Payment,,everything,
'''

COPILOT = '''date,name,amount,status,category,parent category,excluded,tags,type,account,account mask,note,recurring
2026-10-01,Kroger,84.12,posted,Groceries,Food,false,,regular,Visa,1234,,
2026-10-02,Acme,-2450.00,posted,,,false,,income,Checking,9876,,
2026-10-03,Savings,500.00,posted,,,false,,internal transfer,Checking,9876,,
'''

BANK = '''Account Number: ****1234
Statement period: Oct 2026

Posting Date,Description,Debit,Credit,Balance
10/01/2026,KROGER #512 DALLAS TX,84.12,,1000.00
10/02/2026,ACME PAYROLL DIR DEP,,2450.00,3450.00
10/02/2026,STARBUCKS STORE 08821,5.45,,3444.55
10/02/2026,STARBUCKS STORE 08821,5.45,,3439.10
'''


def _by_payee(res):
    return {t.payee: t for t in res.transactions}


def test_detect_formats():
    assert parse_csv_text(YNAB).format == "ynab"
    assert parse_csv_text(MONARCH).format == "monarch"
    assert parse_csv_text(MINT).format == "mint"
    assert parse_csv_text(ROCKET).format == "rocket"
    assert parse_csv_text(COPILOT).format == "copilot"
    assert detect_format(["Date", "Description", "Amount"]) == "generic"


def test_ynab_csv():
    t = _by_payee(parse_csv_text(YNAB))
    assert t["Kroger"].amount == -84.12 and t["Kroger"].category == "Groceries"
    assert t["Employer"].amount == 2450.0
    assert t["Transfer : Savings"].is_transfer
    assert not t["Transfer : Mortgage"].is_transfer  # categorized budget→tracking transfer is spending


def test_monarch_and_mint_signs():
    m = _by_payee(parse_csv_text(MONARCH))
    assert m["Kroger"].amount == -84.12 and m["Employer"].amount == 2450.0 and m["Chase"].is_transfer
    n = _by_payee(parse_csv_text(MINT))
    assert n["Kroger"].amount == -84.12 and n["Acme"].amount == 2450.0


def test_rocket_and_copilot_flip_positive_spending():
    r = parse_csv_text(ROCKET)
    assert r.sign_flipped
    rp = _by_payee(r)
    assert rp["Netflix"].amount == -17.99  # custom name wins
    assert rp["ACME PAYROLL"].amount == 2450.0
    assert rp["CHASE CARD"].is_transfer
    c = parse_csv_text(COPILOT)
    cp = _by_payee(c)
    assert c.sign_flipped and cp["Kroger"].amount == -84.12 and cp["Acme"].category == "Income"
    assert cp["Savings"].is_transfer


def test_bank_csv_with_preamble_and_duplicate_rows(tmp_path):
    res = parse_csv_text(BANK)
    assert res.format == "generic" and not res.sign_flipped and len(res.transactions) == 4
    coffee = [t for t in res.transactions if "STARBUCKS" in t.payee]
    assert len({t.id for t in coffee}) == 2  # identical rows kept distinct
    # Re-importing the same export is idempotent.
    with Store(tmp_path / "t.db") as s:
        assert s.upsert_transactions(res.transactions) == (4, 0)
        assert s.upsert_transactions(parse_csv_text(BANK).transactions) == (0, 4)
        assert s.count() == 4


def test_ynab_api_adapter():
    calls = []

    def fake_get(url, token):
        calls.append(url)
        if url.endswith("/categories"):
            return {"data": {"category_groups": [
                {"name": "Food", "categories": [{"id": "c-groc", "name": "Groceries"}, {"id": "c-din", "name": "Dining Out"}]},
                {"name": "Housing", "categories": [{"id": "c-mort", "name": "Mortgage"}]},
                {"name": "Internal Master Category", "categories": [{"id": "c-rta", "name": "Inflow: Ready to Assign"}]},
            ]}}
        if url.endswith("/accounts"):
            return {"data": {"accounts": [{"id": "a-chk", "on_budget": True}, {"id": "a-visa", "on_budget": True},
                                          {"id": "a-401k", "on_budget": False}]}}
        if "/transactions" in url:
            assert "since_date=2026-09-01" in url
            return {"data": {"transactions": [
                {"id": "t1", "date": "2026-10-01", "amount": -84120, "payee_name": "Kroger", "category_id": "c-groc",
                 "account_id": "a-visa", "account_name": "Visa", "subtransactions": []},
                {"id": "t2", "date": "2026-10-02", "amount": -100000, "payee_name": "Costco", "category_id": None,
                 "category_name": "Split (Multiple Categories)...", "account_id": "a-visa", "account_name": "Visa",
                 "subtransactions": [
                     {"id": "s1", "amount": -70000, "category_id": "c-groc", "payee_name": None},
                     {"id": "s2", "amount": -30000, "category_id": "c-din", "payee_name": None}]},
                {"id": "t3", "date": "2026-10-03", "amount": -900000, "payee_name": "Transfer : Visa",
                 "category_id": None, "transfer_account_id": "a-visa", "account_id": "a-chk", "account_name": "Checking"},
                {"id": "t4", "date": "2026-10-03", "amount": -2150000, "payee_name": "Transfer : Mortgage",
                 "category_id": "c-mort", "transfer_account_id": "a-mortgage", "account_id": "a-chk",
                 "account_name": "Checking"},
                {"id": "t5", "date": "2026-10-04", "amount": 5000000, "payee_name": "Market gains",
                 "account_id": "a-401k", "account_name": "401k"},
                {"id": "t6", "date": "2026-09-01", "amount": 1500000, "payee_name": "Starting Balance",
                 "category_id": "c-rta", "account_id": "a-chk", "account_name": "Checking"},
                {"id": "t7", "date": "2026-10-05", "amount": -5000, "payee_name": "Gone", "deleted": True,
                 "account_id": "a-chk"},
            ]}}
        if "/months/" in url:
            return {"data": {"month": {"categories": [
                {"id": "c-groc", "name": "Groceries", "budgeted": 900000},
                {"id": "c-rta", "name": "Inflow: Ready to Assign", "budgeted": 0},
            ]}}}
        raise AssertionError(url)

    txns, budgets = YNABSource("tok", http_get=fake_get).fetch(date(2026, 9, 1), date(2026, 10, 15))
    by = {t.id: t for t in txns}
    assert set(by) == {"ynab-t1", "ynab-s1", "ynab-s2", "ynab-t3", "ynab-t4", "ynab-t6"}
    assert by["ynab-s1"].payee == "Costco" and by["ynab-s1"].category == "Groceries" and by["ynab-s1"].amount == -70.0
    assert by["ynab-s2"].category == "Dining Out" and by["ynab-s2"].category_group == "Food"
    assert by["ynab-t3"].is_transfer and not by["ynab-t4"].is_transfer
    assert by["ynab-t6"].is_transfer  # starting balance is not income
    assert {(b.month, b.category, b.amount) for b in budgets} == {("2026-09", "Groceries", 900.0),
                                                                  ("2026-10", "Groceries", 900.0)}
    assert all("api.ynab.com/v1/budgets/last-used" in u for u in calls)
