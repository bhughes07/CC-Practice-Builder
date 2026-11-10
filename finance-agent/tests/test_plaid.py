import http.client
import json
import os
import stat
import urllib.request

import pytest

from finagent.cli import main
from finagent.ledger import Ledger
from finagent.sources import plaid as P
from finagent.sources.demo import generate
from finagent.store import Store
from tests.conftest import TODAY

ACCOUNTS = [
    {"account_id": "acc-chk", "name": "Plaid Checking", "mask": "0000", "type": "depository", "subtype": "checking"},
    {"account_id": "acc-cc", "name": "Plaid Credit Card", "mask": "3333", "type": "credit", "subtype": "credit card"},
    {"account_id": "acc-mort", "name": "Plaid Mortgage", "mask": "8888", "type": "loan", "subtype": "mortgage"},
]


def ptx(tid, amount, name, detailed=None, account="acc-cc", day="2026-10-05", pending=False, merchant=None, **kw):
    primary = None
    if detailed:
        for p in ("FOOD_AND_DRINK", "GENERAL_MERCHANDISE", "TRANSFER_OUT", "TRANSFER_IN", "LOAN_PAYMENTS",
                  "INCOME", "BANK_FEES", "RENT_AND_UTILITIES", "ENTERTAINMENT", "GOVERNMENT_AND_NON_PROFIT",
                  "TRANSPORTATION", "GENERAL_SERVICES", "LOAN_DISBURSEMENTS", "PERSONAL_CARE"):
            if detailed.startswith(p):
                primary = p
    return {"transaction_id": tid, "account_id": account, "amount": amount, "date": day, "authorized_date": day,
            "name": name, "merchant_name": merchant, "pending": pending, "original_description": name.upper(),
            "personal_finance_category": {"primary": primary, "detailed": detailed, "version": "v2"} if detailed
            else None, **kw}


class FakePlaid:
    """Scripted stand-in for the Plaid API (records every call)."""

    def __init__(self, pages=None, fail_once_at=None, error=None):
        self.calls = []
        self.pages = pages or {}
        self.fail_once_at = fail_once_at
        self.error = error

    def __call__(self, path, body):
        self.calls.append((path, json.loads(json.dumps(body))))
        if self.error:
            raise self.error
        if path == "/link/token/create":
            return {"link_token": "link-sandbox-123", "expiration": "x"}
        if path == "/item/public_token/exchange":
            return {"access_token": "access-sandbox-abc", "item_id": "item-1"}
        if path == "/sandbox/public_token/create":
            return {"public_token": "public-sandbox-xyz"}
        if path == "/item/remove":
            return {"request_id": "r"}
        if path == "/transactions/sync":
            cursor = body.get("cursor")
            if self.fail_once_at is not None and cursor == self.fail_once_at:
                self.fail_once_at = None
                raise P.PlaidError("TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION", "changed")
            return self.pages[cursor]
        raise AssertionError(path)


def client(fake, env="sandbox"):
    return P.PlaidClient("cid", "sec", env, transport=fake)


def item():
    return P.new_item("access-1", "item-1", "sandbox", {
        "institution": {"name": "First Platypus Bank", "institution_id": "ins_109508"},
        "accounts": [{"id": a["account_id"], "name": a["name"], "mask": a["mask"], "type": a["type"],
                      "subtype": a["subtype"]} for a in ACCOUNTS]})


def test_link_token_requests():
    fake = FakePlaid()
    c = client(fake)
    assert c.create_link_token("finagent-abc", "finagent household app name too long", ["US"], 730) == "link-sandbox-123"
    path, body = fake.calls[-1]
    assert body["user"] == {"client_user_id": "finagent-abc"} and body["products"] == ["transactions"]
    assert body["transactions"] == {"days_requested": 730} and len(body["client_name"]) <= 30
    assert body["country_codes"] == ["US"] and body["language"] == "en" and "access_token" not in body
    c.create_link_token("finagent-abc", access_token="access-1")
    _, update = fake.calls[-1]
    assert update["access_token"] == "access-1" and "products" not in update and "transactions" not in update


@pytest.mark.parametrize("detailed,merchant,amount,expected", [
    ("FOOD_AND_DRINK_RESTAURANT", "Chipotle", -20, ("Dining Out", "Food", False)),
    ("FOOD_AND_DRINK_GROCERIES", "Kroger", -80, ("Groceries", "Food", False)),
    ("INCOME_WAGES", None, 2000, ("Paycheck", "Income", False)),          # v1 name
    ("INCOME_SALARY", None, 2000, ("Paycheck", "Income", False)),         # v2 name
    ("LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", None, -900, ("Credit Card Payment", "Transfers", True)),
    ("LOAN_PAYMENTS_MORTGAGE_PAYMENT", None, -2150, ("Mortgage", "Housing", False)),
    ("TRANSFER_OUT_ACCOUNT_TRANSFER", None, -300, ("Transfer", "Transfers", True)),
    ("TRANSFER_OUT_TRANSFER_OUT_FROM_APPS", "Venmo", -60, ("Payments to People", "Other", False)),
    ("TRANSFER_OUT_ACCOUNT_TRANSFER", "Zelle", -60, ("Payments to People", "Other", False)),
    ("TRANSFER_IN_ACCOUNT_TRANSFER", "Venmo", 60, ("Transfer", "Transfers", True)),
    ("TRANSFER_OUT_WITHDRAWAL", None, -100, ("Cash & ATM", "Other", False)),
    ("LOAN_DISBURSEMENTS_PERSONAL", None, 5000, ("Transfer", "Transfers", True)),
    ("BANK_FEES_OVERDRAFT_FEES", None, -35, ("Overdraft Fees", "Financial", False)),
    ("GOVERNMENT_AND_NON_PROFIT_DONATIONS", None, -800, ("Giving", "Giving", False)),
    ("GENERAL_MERCHANDISE_SUPERSTORES", "Target", -50, ("Shopping", "Shopping", False)),
    (None, None, -12, ("Uncategorized", "", False)),
])
def test_category_mapping(detailed, merchant, amount, expected):
    pfc = {"primary": detailed.split("_")[0] if detailed else None, "detailed": detailed} if detailed else None
    if detailed:
        for p in ("FOOD_AND_DRINK", "LOAN_PAYMENTS", "TRANSFER_OUT", "TRANSFER_IN", "LOAN_DISBURSEMENTS",
                  "BANK_FEES", "GOVERNMENT_AND_NON_PROFIT", "GENERAL_MERCHANDISE", "INCOME"):
            if detailed.startswith(p):
                pfc["primary"] = p
    assert P._classify(pfc, merchant or "", amount) == expected


def test_sync_pages_mutation_restart_pending_and_accounts(tmp_path):
    pages = {
        None: {"added": [
            ptx("t1", 84.12, "KROGER #512", "FOOD_AND_DRINK_GROCERIES", merchant="Kroger"),
            ptx("t2", -2450.00, "ACME PAYROLL", "INCOME_SALARY", account="acc-chk"),
            ptx("t3", 2150.00, "MORTGAGE PMT", "LOAN_PAYMENTS_MORTGAGE_PAYMENT", account="acc-chk"),
            ptx("t3b", -2150.00, "PAYMENT RECEIVED", "LOAN_PAYMENTS_MORTGAGE_PAYMENT", account="acc-mort"),
        ], "modified": [], "removed": [], "next_cursor": "c1", "has_more": True, "accounts": ACCOUNTS,
            "transactions_update_status": "INITIAL_UPDATE_COMPLETE"},
        "c1": {"added": [
            ptx("t4", 900.00, "CHASE CARD PAYMENT", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", account="acc-chk"),
            ptx("t5", 18.40, "STARBUCKS", "FOOD_AND_DRINK_COFFEE", merchant="Starbucks", pending=True,
                day="2026-10-06"),
            ptx("t6", -45.99, "AMAZON REFUND", "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES", merchant="Amazon"),
            ptx("t4b", -900.00, "PAYMENT RECEIVED", "TRANSFER_IN_ACCOUNT_TRANSFER", account="acc-cc"),
        ], "modified": [], "removed": [], "next_cursor": "c2", "has_more": False, "accounts": ACCOUNTS,
            "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE"},
        "c2": {"added": [ptx("t7", 21.15, "STARBUCKS", "FOOD_AND_DRINK_COFFEE", merchant="Starbucks",
                             day="2026-10-06", pending_transaction_id="t5")],
               "modified": [ptx("t1", 86.12, "KROGER #512", "FOOD_AND_DRINK_GROCERIES", merchant="Kroger")],
               "removed": [{"transaction_id": "t5", "account_id": "acc-cc"}],
               "next_cursor": "c3", "has_more": False, "accounts": ACCOUNTS,
               "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE"},
    }
    fake = FakePlaid(pages, fail_once_at="c1")
    it = item()
    with Store(tmp_path / "f.db") as store:
        res = P.sync_item(client(fake), it, store)
        cursors = [b.get("cursor") for p, b in fake.calls if p == "/transactions/sync"]
        assert cursors == [None, "c1", None, "c1"]  # restarted from the ORIGINAL cursor
        assert it["cursor"] == "c2" and res.added == 7 and it["status"] == "ok"
        rows = {t.id.split(":")[-1]: t for t in store.transactions()}
        assert "t3b" not in rows  # loan-account side is a balance tracker, not spending
        assert rows["t1"].amount == -84.12 and rows["t1"].payee == "Kroger" and rows["t1"].memo == "KROGER #512"
        assert rows["t1"].account == "Plaid Credit Card ••3333"
        assert rows["t2"].amount == 2450.0 and rows["t2"].category == "Paycheck"
        assert rows["t4"].is_transfer and rows["t6"].amount == 45.99 and rows["t5"].pending

        P.sync_item(client(fake), it, store)  # pending posts with a tip, Kroger amount corrected
        rows = {t.id.split(":")[-1]: t for t in store.transactions()}
        assert "t5" not in rows and rows["t7"].amount == -21.15 and rows["t1"].amount == -86.12
        assert it["cursor"] == "c3"

        ledger = Ledger(store.transactions(), [], {"household": {"currency": "$"}, "analysis": {}}, TODAY)
        kinds = {t.id.split(":")[-1]: t.kind for t in ledger.all}
        assert kinds["t2"] == "income" and kinds["t4"] == "transfer" and kinds["t3"] == "expense"


def test_relink_needed_is_reported_not_raised(tmp_path):
    fake = FakePlaid(error=P.PlaidError("ITEM_LOGIN_REQUIRED", "the login details of this item have changed"))
    it = item()
    with Store(tmp_path / "f.db") as store:
        res = P.sync_item(client(fake), it, store)
    assert res.needs_relink and it["status"] == "relink"
    assert 'finagent plaid link --update "First Platypus Bank"' in res.line()


def test_items_file_is_private_and_stable(tmp_path):
    path = tmp_path / "data" / "plaid.json"
    items = P.PlaidItems(path)
    uid = items.client_user_id
    items.upsert(item())
    items.save()
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    again = P.PlaidItems(path)
    assert again.client_user_id == uid and again.items[0]["access_token"] == "access-1"
    assert again.find("platypus")[0]["item_id"] == "item-1" and again.find("chase") == []


def _post(url, body, host=None, ctype="application/json"):
    u = urllib.request.urlparse(url)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
    headers = {"Content-Type": ctype}
    if host:
        headers["Host"] = host
    conn.request("POST", u.path, body=json.dumps(body), headers=headers)
    r = conn.getresponse()
    return r.status, json.loads(r.read() or b"{}")


def test_local_link_page_flow(tmp_path):
    fake = FakePlaid()
    items = P.PlaidItems(tmp_path / "plaid.json")
    s = P.LinkSession(client(fake), items, port=0)
    s.start()
    try:
        html = urllib.request.urlopen(s.url, timeout=5).read().decode()
        assert P.LINK_JS in html and "Connect your bank" in html
        base = s.url.rstrip("/")
        root = s.url.split(s.nonce)[0]
        assert _post(root + "wrong/token", {})[0] == 404  # random path required
        assert _post(base + "/token", {}, ctype="text/plain")[0] == 403  # JSON only
        assert _post(base + "/token", {}, host="evil.example:80")[0] == 403  # DNS-rebinding guard
        assert _post(base + "/token", {}) == (200, {"link_token": "link-sandbox-123"})
        meta = {"institution": {"name": "Chase", "institution_id": "ins_56"},
                "accounts": [{"id": "a1", "name": "Total Checking", "mask": "1234", "type": "depository",
                              "subtype": "checking"}]}
        code, out = _post(base + "/exchange", {"public_token": "public-1", "metadata": meta})
        assert code == 200 and out == {"ok": True, "institution": "Chase", "accounts": ["Total Checking ••1234"]}
        saved = P.PlaidItems(tmp_path / "plaid.json").items
        assert saved[0]["access_token"] == "access-sandbox-abc" and saved[0]["institution"]["name"] == "Chase"
        assert _post(base + "/done", {})[1] == {"ok": True} and s.done.is_set()
    finally:
        s.stop()
    assert [i["institution"]["name"] for i in s.linked] == ["Chase"]


# --------------------------------------------------------------------------- end to end through the CLI

_PFC = {
    "Groceries": "FOOD_AND_DRINK_GROCERIES", "Dining Out": "FOOD_AND_DRINK_RESTAURANT",
    "Coffee Shops": "FOOD_AND_DRINK_COFFEE", "Paycheck": "INCOME_SALARY", "Tithe": "GOVERNMENT_AND_NON_PROFIT_DONATIONS",
    "Missions": "GOVERNMENT_AND_NON_PROFIT_DONATIONS", "Mortgage": "LOAN_PAYMENTS_MORTGAGE_PAYMENT",
    "Subscriptions": "ENTERTAINMENT_TV_AND_MOVIES", "Shopping": "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES",
    "Gas & Fuel": "TRANSPORTATION_GAS", "Electric": "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY",
    "Water": "RENT_AND_UTILITIES_WATER", "Internet": "RENT_AND_UTILITIES_INTERNET_AND_CABLE",
    "Phone": "RENT_AND_UTILITIES_TELEPHONE", "Car Payment": "LOAN_PAYMENTS_CAR_PAYMENT",
    "Car Insurance": "GENERAL_SERVICES_INSURANCE", "Bank Fees": "BANK_FEES_OTHER_BANK_FEES",
    "Gym": "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS", "Medical": "MEDICAL_PRIMARY_CARE",
    "Auto Maintenance": "GENERAL_SERVICES_AUTOMOTIVE",
}


def _demo_as_plaid():
    txns, _ = generate(TODAY)
    out = []
    for t in txns:
        if t.is_transfer:
            detailed = "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT" if "PAYMENT" in t.payee else "TRANSFER_OUT_ACCOUNT_TRANSFER"
        else:
            detailed = _PFC.get(t.category, "GENERAL_SERVICES_OTHER_GENERAL_SERVICES")
        out.append(ptx(t.id, -t.amount, t.payee, detailed, account="acc-chk" if t.account == "Checking" else "acc-cc",
                       day=t.date.isoformat()))
    return out


def test_cli_sandbox_sync_and_review(tmp_path, monkeypatch, capsys):
    fake = FakePlaid({None: {"added": _demo_as_plaid(), "modified": [], "removed": [], "next_cursor": "c1",
                             "has_more": False, "accounts": ACCOUNTS,
                             "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE"},
                      "c1": {"added": [], "modified": [], "removed": [], "next_cursor": "c1", "has_more": False,
                             "accounts": ACCOUNTS, "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE"}})
    monkeypatch.setattr("finagent.cli._plaid_client", lambda cfg: client(fake))
    cfg = tmp_path / "config.toml"
    cfg.write_text('[source]\ntype = ["plaid"]\n[plaid]\nenv = "sandbox"\n[report]\nformats = ["html"]\n')
    base = ["--config", str(cfg), "--today", TODAY.isoformat()]

    assert main(base + ["plaid", "sandbox"]) == 0
    out = capsys.readouterr().out
    assert "Connected Plaid's test bank" in out and "new" in out
    assert main(base + ["plaid", "list"]) == 0
    out = capsys.readouterr().out
    assert "First Platypus Bank (sandbox)" in out and "connected" in out

    monkeypatch.setattr("finagent.sources.plaid.make_client", lambda cfg, transport=None: client(fake))
    assert main(base + ["sync"]) == 0
    assert "0 new" in capsys.readouterr().out  # incremental: nothing changed since the cursor

    assert main(base + ["review", "--out", str(tmp_path / "r")]) == 0
    out = capsys.readouterr().out
    assert "Dining Out is up" in out
    html = next((tmp_path / "r").glob("review-*.html")).read_text()
    assert "Netflix raised its price" in html and "Possible double charge" in html

    assert main(base + ["plaid", "remove", "platypus", "--delete-data"]) == 0
    assert "Deleted" in capsys.readouterr().out
    assert not (tmp_path / "data" / "finance.db").exists()  # sandbox never touches the real database
    assert (tmp_path / "data" / "plaid-sandbox.json").exists()
    with Store(tmp_path / "data" / "sandbox.db") as store:
        assert store.count() == 0
    assert ("/item/remove", {"access_token": "access-sandbox-abc"}) in fake.calls


def test_sync_without_links_or_keys_explains(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[source]\ntype = "plaid"\n')
    monkeypatch.delenv("PLAID_CLIENT_ID", raising=False)
    monkeypatch.delenv("PLAID_SECRET", raising=False)
    assert main(["--config", str(cfg), "sync"]) == 0
    assert "finagent plaid link" in capsys.readouterr().out
    assert main(["--config", str(cfg), "plaid", "link", "--no-browser"]) == 2
    assert "PLAID_CLIENT_ID" in capsys.readouterr().err


def test_scheduled_run_surfaces_relink_in_report(tmp_path, monkeypatch, capsys):
    path = tmp_path / "data" / "plaid.json"
    items = P.PlaidItems(path)
    it = item()
    it["env"] = "production"
    items.upsert(it)
    items.save()
    from finagent.sources.demo import generate as gen
    txns, _ = gen(TODAY)
    with Store(tmp_path / "data" / "finance.db") as store:
        store.upsert_transactions(txns)
    fake = FakePlaid(error=P.PlaidError("ITEM_LOGIN_REQUIRED", "login changed"))
    monkeypatch.setattr("finagent.sources.plaid.make_client", lambda cfg, transport=None: client(fake, "production"))
    cfg = tmp_path / "config.toml"
    cfg.write_text('[source]\ntype = "plaid"\n[report]\nformats = ["html"]\n')
    assert main(["--config", str(cfg), "--today", TODAY.isoformat(), "run", "weekly", "--out", str(tmp_path / "r")]) == 0
    html = next((tmp_path / "r").glob("weekly-*.html")).read_text()
    assert "Some accounts didn&#x27;t sync" in html and "finagent plaid link --update" in html
    assert P.PlaidItems(path).items[0]["status"] == "relink"


def test_sandbox_never_writes_to_real_database_even_without_config(tmp_path, monkeypatch, capsys):
    """Regression: with no config.toml (source.type defaulted), sandbox data landed in finance.db."""
    fake = FakePlaid({None: {"added": [ptx("t1", 12.5, "TEST", "FOOD_AND_DRINK_COFFEE")], "modified": [],
                             "removed": [], "next_cursor": "c1", "has_more": False, "accounts": ACCOUNTS,
                             "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE"}})
    monkeypatch.setattr("finagent.cli._plaid_client", lambda cfg: client(fake))
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    monkeypatch.setattr("finagent.config.home_dir", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["plaid", "sandbox"]) == 0
    assert (tmp_path / "data" / "sandbox.db").exists()
    assert not (tmp_path / "data" / "finance.db").exists()
    assert (tmp_path / "data" / "plaid-sandbox.json").exists() and not (tmp_path / "data" / "plaid.json").exists()
