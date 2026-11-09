"""Synthetic household data so you can see every report before connecting real data.

The data is deterministic for a given `today` and deliberately contains the
patterns the agent is built to catch: a subscription price hike, a new
subscription, a cancelled one, several Apple charges under one merchant, a
duplicate charge, bank fees, a big one-off car repair, and dining/delivery
spending creeping up over the last three months.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from finagent.models import Budget, Transaction
from finagent.util import add_months, month_key, stable_id

DEMO_BUDGETS: dict[tuple[str, str], float] = {
    ("Giving", "Tithe"): 800, ("Giving", "Missions"): 50,
    ("Housing", "Mortgage"): 2150, ("Housing", "Home Maintenance"): 150,
    ("Utilities", "Electric"): 220, ("Utilities", "Water"): 80, ("Utilities", "Internet"): 80,
    ("Utilities", "Phone"): 185,
    ("Food", "Groceries"): 1000, ("Food", "Dining Out"): 400, ("Food", "Coffee Shops"): 60,
    ("Transportation", "Gas & Fuel"): 320, ("Transportation", "Car Payment"): 465,
    ("Transportation", "Car Insurance"): 213, ("Transportation", "Auto Maintenance"): 100,
    ("Family", "Kids Activities"): 205, ("Family", "Medical"): 100, ("Family", "Clothing"): 150,
    ("Lifestyle", "Shopping"): 350, ("Lifestyle", "Entertainment"): 120, ("Lifestyle", "Subscriptions"): 110,
    ("Lifestyle", "Gym"): 25, ("Lifestyle", "Gifts"): 75,
}

_ELECTRIC_BY_MONTH = {1: 190, 2: 170, 3: 140, 4: 130, 5: 165, 6: 240, 7: 300, 8: 315, 9: 260, 10: 175,
                      11: 140, 12: 180}


def generate(today: date | None = None, months: int = 12, seed: int = 7) -> tuple[list[Transaction], list[Budget]]:
    today = today or date.today()
    rng = random.Random(f"{seed}-{today.isoformat()}")
    start = add_months(date(today.year, today.month, 1), -months)
    end = today - timedelta(days=1)
    recent = add_months(date(today.year, today.month, 1), -3)  # spending creep starts here
    txns: list[Transaction] = []
    counter = [0]

    def add(d: date, amount: float, payee: str, category: str = "", group: str = "",
            account: str = "Visa Card", transfer: bool = False, memo: str = "") -> None:
        if not (start <= d <= end):
            return
        counter[0] += 1
        txns.append(Transaction(
            id="demo-" + stable_id(d, payee, amount, counter[0]), date=d, amount=round(amount, 2),
            payee=payee, category=category, category_group=group, account=account, memo=memo,
            source="demo", is_transfer=transfer))

    def day(m: date, dom: int) -> date:
        return date(m.year, m.month, min(dom, 28))

    def rand_day(m: date) -> date:
        return date(m.year, m.month, rng.randint(1, 28))

    # Biweekly paycheck (Fridays)
    d = start + timedelta(days=(4 - start.weekday()) % 7)
    while d <= end:
        add(d, 2450.00, "ACME ENGINEERING PAYROLL", "Paycheck", "Income", "Checking")
        d += timedelta(days=14)

    m = start
    idx = 0
    while m <= end:
        is_recent = m >= recent
        # Semi-monthly paycheck
        add(day(m, 1), 1650.00, "LAKESIDE SCHOOLS PAYROLL", "Paycheck", "Income", "Checking")
        add(day(m, 15), 1650.00, "LAKESIDE SCHOOLS PAYROLL", "Paycheck", "Income", "Checking")

        # Giving (protected — never suggested as a cut)
        add(day(m, 1), -800.00, "Online Giving - Church", "Tithe", "Giving", "Checking")
        add(day(m, 15), -50.00, "Online Giving - Missions", "Missions", "Giving", "Checking")

        # Fixed bills
        add(day(m, 1), -2150.00, "LAKEVIEW MORTGAGE", "Mortgage", "Housing", "Checking")
        add(day(m, 2), -300.00, "Transfer to Savings", "", "", "Checking", transfer=True)
        e = _ELECTRIC_BY_MONTH[m.month] * rng.uniform(0.93, 1.07)
        add(day(m, 18), -e, "TXU ENERGY RETAIL", "Electric", "Utilities", "Checking")
        add(day(m, 22), -rng.uniform(55, 95), "CITY OF LAKESIDE UTILITIES", "Water", "Utilities", "Checking")
        add(day(m, 12), -79.99, "SPECTRUM", "Internet", "Utilities")
        add(day(m, 20), -185.43, "VERIZON WIRELESS", "Phone", "Utilities")
        add(day(m, 8), -212.40, "GEICO *AUTO", "Car Insurance", "Transportation")
        add(day(m, 15), -465.00, "TOYOTA FINANCIAL SERVICES", "Car Payment", "Transportation", "Checking")
        add(day(m, 5), -85.00, "LAKESIDE YOUTH SOCCER", "Kids Activities", "Family")
        add(day(m, 1), -120.00, "HARMONY MUSIC STUDIO", "Kids Activities", "Family", "Checking")

        # Subscriptions
        price_hike = m >= add_months(date(today.year, today.month, 1), -2)
        add(day(m, 9), -(17.99 if price_hike else 15.49), "NETFLIX.COM", "Subscriptions", "Lifestyle")
        add(day(m, 3), -16.99, "SPOTIFY USA", "Subscriptions", "Lifestyle")
        add(day(m, 21), -13.99, "DISNEY PLUS", "Subscriptions", "Lifestyle")
        add(day(m, 21), -9.99, "HULU 877-8244858", "Subscriptions", "Lifestyle")
        add(day(m, 6), -13.99, "GOOGLE *YouTubePremium", "Subscriptions", "Lifestyle")
        add(day(m, 11), -2.99, "APPLE.COM/BILL", "Subscriptions", "Lifestyle")
        add(day(m, 26), -9.99, "APPLE.COM/BILL", "Subscriptions", "Lifestyle")
        add(day(m, 19), -14.95, "AUDIBLE*2K4LM90", "Subscriptions", "Lifestyle")
        if m >= add_months(date(today.year, today.month, 1), -3):
            add(day(m, 14), -16.99, "MAX.COM", "Subscriptions", "Lifestyle")  # new; trial converted
        if m < add_months(date(today.year, today.month, 1), -5):
            add(day(m, 24), -7.99, "PEACOCK", "Subscriptions", "Lifestyle")  # cancelled
        add(day(m, 17), -24.99, "PLANET FITNESS", "Gym", "Lifestyle")
        if idx == 4:
            add(day(m, 17), -49.00, "PLANET FITNESS ANNUAL FEE", "Gym", "Lifestyle")
        if idx == 1:
            add(day(m, 10), -139.00, "Amazon Prime*2K41L", "Subscriptions", "Lifestyle")

        # Groceries (~9 trips, creeping up slightly)
        for _ in range(rng.randint(8, 10)):
            store = rng.choices(["KROGER #512", "H-E-B #0231", "ALDI 77031", "TOM THUMB #2214"],
                                weights=[5, 3, 2, 1])[0]
            add(rand_day(m), -rng.uniform(45, 175) * (1.06 if is_recent else 1.0), store, "Groceries", "Food")
        add(rand_day(m), -rng.uniform(180, 320), "COSTCO WHSE #1084", "Groceries", "Food")

        # Dining out + delivery (the creep)
        restaurants = ["CHICK-FIL-A #01234", "CHIPOTLE 2231", "WHATABURGER 112", "TST* FIREBIRD GRILL",
                       "SQ *LOCAL TACO CO", "OLIVE GARDEN 0441", "RAISING CANES 0291"]
        for _ in range(rng.randint(12, 14) if is_recent else rng.randint(7, 9)):
            add(rand_day(m), -rng.uniform(14, 78), rng.choice(restaurants), "Dining Out", "Food")
        for _ in range(rng.randint(7, 9) if is_recent else rng.randint(2, 4)):
            add(rand_day(m), -rng.uniform(28, 62), rng.choice(["DOORDASH*DASHPASS", "UBER *EATS PENDING"]),
                "Dining Out", "Food")
        for _ in range(rng.randint(13, 17)):
            add(rand_day(m), -rng.uniform(5.25, 8.95), "STARBUCKS STORE 08821", "Coffee Shops", "Food")

        # Transportation
        for _ in range(rng.randint(4, 6)):
            add(rand_day(m), -rng.uniform(38, 68), rng.choice(["SHELL OIL 57442", "QT 0871", "BUC-EE'S #22"]),
                "Gas & Fuel", "Transportation")
        if idx % 3 == 0:
            add(rand_day(m), -79.99, "TAKE 5 OIL CHANGE", "Auto Maintenance", "Transportation")

        # Family
        for _ in range(rng.randint(1, 2)):
            add(rand_day(m), -rng.uniform(12, 45), "CVS/PHARMACY #0912", "Medical", "Family")
        if rng.random() < 0.4:
            add(rand_day(m), -40.00, "LAKESIDE PEDIATRICS", "Medical", "Family")
        for _ in range(rng.randint(0, 2)):
            add(rand_day(m), -rng.uniform(25, 95), rng.choice(["OLD NAVY 3321", "TARGET 00012345"]),
                "Clothing", "Family")

        # Lifestyle
        for _ in range(rng.randint(10, 13) if is_recent else rng.randint(7, 10)):
            add(rand_day(m), -rng.uniform(9, 95), f"AMZN Mktp US*{rng.randint(10000, 99999)}", "Shopping",
                "Lifestyle")
        for _ in range(rng.randint(2, 4)):
            add(rand_day(m), -rng.uniform(20, 140), "TARGET 00012345", "Shopping", "Lifestyle")
        for _ in range(rng.randint(1, 3)):
            add(rand_day(m), -rng.uniform(28, 64), "CINEMARK THEATRES 412", "Entertainment", "Lifestyle")
        for _ in range(rng.randint(0, 2) + (5 if m.month == 12 else 0)):
            add(rand_day(m), -rng.uniform(25, 90), rng.choice(["TARGET 00012345", "AMZN Mktp US*GIFT"]),
                "Gifts", "Lifestyle")
        for _ in range(rng.randint(0, 2)):
            add(rand_day(m), -rng.uniform(20, 180), rng.choice(["THE HOME DEPOT #6521", "LOWES #01842"]),
                "Home Maintenance", "Housing")

        # Card payment (transfer — excluded from spending)
        add(day(m, 25), -rng.uniform(2600, 3100), "VISA CARD PAYMENT", "", "", "Checking", transfer=True)
        add(day(m, 25), rng.uniform(2600, 3100), "PAYMENT THANK YOU", "", "", "Visa Card", transfer=True)

        m = add_months(m, 1)
        idx += 1

    # One-offs anchored to today so the reports always have something to show.
    first = date(today.year, today.month, 1)
    add(add_months(first, -3) + timedelta(days=9), -1240.18, "FIRESTONE COMPLETE AUTO #0331",
        "Auto Maintenance", "Transportation")
    add(add_months(first, -4) + timedelta(days=13), -35.00, "OVERDRAFT FEE", "Bank Fees", "Financial", "Checking")
    add(add_months(first, -2) + timedelta(days=3), -29.00, "LATE FEE", "Bank Fees", "Financial")
    add(add_months(first, -5) + timedelta(days=20), -4.12, "FOREIGN TRANSACTION FEE", "Bank Fees", "Financial")
    for back in (6, 3):
        add(add_months(first, -back) + timedelta(days=11), -3.50, "NON-NETWORK ATM FEE", "Bank Fees", "Financial",
            "Checking")
    dup = today - timedelta(days=24)
    add(dup, -89.99, "BEST BUY 00004523", "Shopping", "Lifestyle")
    add(dup + timedelta(days=1), -89.99, "BEST BUY 00004523", "Shopping", "Lifestyle")
    add(today - timedelta(days=52), 45.99, "AMZN Mktp US Refund", "Shopping", "Lifestyle", memo="Return")
    lw = today - timedelta(days=today.weekday() + 7)  # Monday of last week
    add(lw + timedelta(days=2), -3.50, "NON-NETWORK ATM FEE", "Bank Fees", "Financial", "Checking")
    add(lw + timedelta(days=5), -164.27, "SQ *LAKESIDE STEAKHOUSE", "Dining Out", "Food")

    budgets = []
    bm = start
    while bm <= end:
        for (grp, cat), amt in DEMO_BUDGETS.items():
            budgets.append(Budget(month=month_key(bm), category=cat, amount=amt, category_group=grp))
        bm = add_months(bm, 1)
    txns.sort(key=lambda t: (t.date, t.id))
    return txns, budgets
