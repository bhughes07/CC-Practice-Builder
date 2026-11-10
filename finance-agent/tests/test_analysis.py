from datetime import date, timedelta

from finagent.categorize import enrich
from finagent.detectors import find_duplicates, find_fees, find_unusual
from finagent.recurring import detect_recurring
from tests.conftest import TODAY, tx


def _monthly(payee, amounts, category="Subscriptions", day=9, start=(2026, 1)):
    y, m = start
    out = []
    for i, a in enumerate(amounts):
        mm = m + i
        out.append(tx(date(y + (mm - 1) // 12, (mm - 1) % 12 + 1, day), -a, payee, category, n=i))
    return out


def test_recurring_price_increase_and_new_and_cancelled(cfg):
    rows = (_monthly("NETFLIX.COM", [15.49] * 8 + [17.99, 17.99]) +
            _monthly("MAX.COM", [16.99] * 3, start=(2026, 8), day=14) +
            _monthly("PEACOCK", [7.99] * 4, day=24))
    rec = {r.merchant: r for r in detect_recurring(enrich(rows, cfg), TODAY)}
    assert rec["Netflix"].price_change == 2.5 and rec["Netflix"].previous_amount == 15.49
    assert rec["Netflix"].active and rec["Netflix"].monthly_cost == 17.99
    assert rec["Max"].is_new and rec["Max"].active
    assert not rec["Peacock"].active


def test_two_plans_under_one_merchant(cfg):
    rows = _monthly("APPLE.COM/BILL", [2.99] * 10, day=11) + _monthly("APPLE.COM/BILL", [9.99] * 10, day=26)
    found = sorted(r.last_amount for r in detect_recurring(enrich(rows, cfg), TODAY) if r.merchant == "Apple")
    assert found == [2.99, 9.99]


def test_variable_shopping_is_not_recurring(cfg):
    import random
    rng = random.Random(1)
    rows = [tx(date(2026, 5, 1) + timedelta(days=rng.randint(0, 180)), -rng.uniform(40, 180), "KROGER #512",
               "Groceries", n=i) for i in range(60)]
    assert not detect_recurring(enrich(rows, cfg), TODAY)


def test_fees_duplicates_unusual(cfg):
    rows = enrich([
        tx("2026-10-01", -35, "OVERDRAFT FEE", "Bank Fees"),
        tx("2026-10-02", -3.5, "NON-NETWORK ATM FEE"),
        tx("2026-10-03", -49, "PLANET FITNESS ANNUAL FEE", "Gym"),  # membership, not a bank fee
        tx("2026-10-16", -89.99, "BEST BUY 00004523", "Shopping", n=1),
        tx("2026-10-17", -89.99, "BEST BUY 00004523", "Shopping", n=2),
        *[tx(f"2026-10-{d:02d}", -5.45, "STARBUCKS", "Coffee", n=d) for d in range(1, 11)],  # routine, not dupes
        *[tx(f"2026-0{m}-15", -45.0, "CHIPOTLE", "Dining Out", n=m) for m in range(5, 10)],
        tx("2026-10-20", -240.0, "STEAKHOUSE", "Dining Out"),
        *[tx(f"2026-0{m}-19", -280.0, "COSTCO WHSE", "Groceries", n=m) for m in range(5, 10)],
        *[tx(f"2026-0{m}-03", -90.0, "KROGER", "Groceries", n=m) for m in range(5, 10)],
        tx("2026-10-19", -290.0, "COSTCO WHSE", "Groceries", n=99),
    ], cfg)
    start, end = date(2026, 10, 1), date(2026, 10, 31)
    fees = {f.type for f in find_fees(rows, start, end)}
    assert fees == {"overdraft", "atm"}
    dups = find_duplicates(rows, start, end)
    assert [(a.merchant, b.outflow) for a, b in dups] == [("Best Buy", 89.99)]
    unusual = [t.merchant for t, _ in find_unusual(rows, start, end, 150)]
    assert "Steakhouse" in unusual and "Costco" not in unusual


def test_demo_insights_find_planted_patterns(demo):
    ledger, A = demo
    keys = {i.key for i in A.insights}
    assert "category:Dining Out" in keys
    assert "price:Netflix" in keys
    assert "new:Max" in keys
    assert "streaming-stack" in keys
    assert "fees" in keys
    assert any(k.startswith("dup:") for k in keys)
    assert "sinking-fund" in keys
    assert any(w.title.startswith("Peacock") for w in A.wins)
    assert A.potential_monthly > 0 and A.potential_one_time == 89.99
    # Ranked by impact
    scores = [i.score for i in A.insights]
    assert scores == sorted(scores, reverse=True)


def test_giving_and_savings_never_suggested_as_cuts(demo):
    ledger, A = demo
    for i in A.insights:
        assert "Tithe" not in i.area and "Missions" not in i.area and "Giving" not in i.title
    assert ledger.is_protected("Tithe") and ledger.is_protected("Missions")
    assert not ledger.is_protected("Dining Out")


def test_transfers_excluded_from_spending(demo):
    ledger, A = demo
    oct_spend = ledger.spend_month("2026-10")
    payees = {t.payee for t in ledger.expenses_in_month("2026-10")}
    assert "Transfer to Savings" not in payees and "VISA CARD PAYMENT" not in payees
    assert 7000 < oct_spend < 11000


def test_subscription_billed_in_bursts_is_flagged(cfg):
    from finagent.detectors import find_repeat_billing
    from finagent.insights import Analysis
    from finagent.ledger import Ledger
    burst = {"2026-05": [29, 29], "2026-06": [29, 29], "2026-07": [30, 30, 30, 30, 31], "2026-08": [23, 30, 30, 31, 31],
             "2026-09": [23, 29, 29, 30, 30]}
    rows = [tx(f"{mo}-{d:02d}", -35.71, "HLU*HULUPLUS hulu.com/billCA", "Streaming & Movies", account="Visa ••1",
               n=i * 10 + j) for i, (mo, days) in enumerate(burst.items()) for j, d in enumerate(days)]
    rows += [tx(f"2026-{m:02d}-18", -21.64, "Hulu 877-8244858 CA", "Streaming & Movies", account="Visa ••1", n=m)
             for m in range(5, 11)]                                               # a second plan: once a month
    rows += [tx(f"2026-{m:02d}-07", -25.92, "DISNEY PLUS", "Streaming & Movies", n=m) for m in range(5, 11)]
    rows += [tx(f"2026-{m:02d}-{d:02d}", -16.50, "STARBUCKS", "Coffee", n=m * 100 + d)  # two coffees, not a sub
             for m in range(5, 11) for d in (3, 3, 4)]
    rows = enrich(rows, cfg)
    found = find_repeat_billing(rows, date(2026, 5, 1), date(2026, 10, 31))
    assert [(r.merchant, r.amount, len(r.extras), r.bursts) for r in found] == [("Hulu", 35.71, 12, 5)]
    assert round(sum(t.outflow for t in found[0].extras), 2) == 428.52

    L = Ledger(rows, [], cfg, date(2026, 11, 10))
    A = Analysis(L, L.available_months(6))
    top = next(i for i in A.insights if i.key.startswith("repeat:"))
    assert top.kind == "recover" and top.one_time == 428.52 and "12 extra charges" in top.title
