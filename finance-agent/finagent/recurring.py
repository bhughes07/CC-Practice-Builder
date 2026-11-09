"""Find subscriptions and recurring bills from raw transaction history."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from finagent.models import (
    CLASS_DEBT, CLASS_DISCRETIONARY, CLASS_FEES, CLASS_GIVING, Transaction,
)
from finagent.util import add_months, cv, mean, median

# name, nominal days, min gap, max gap, occurrences per month
CADENCES = [
    ("weekly", 7, 5, 9, 52 / 12),
    ("biweekly", 14, 12, 17, 26 / 12),
    ("monthly", 30.44, 25, 36, 1.0),
    ("quarterly", 91, 80, 102, 1 / 3),
    ("semiannual", 182, 160, 205, 1 / 6),
    ("annual", 365, 335, 395, 1 / 12),
]

KNOWN_SUBSCRIPTIONS = {
    "Netflix", "Spotify", "Hulu", "Disney+", "Max", "YouTube", "Paramount+", "Peacock", "Audible", "iCloud",
    "Apple", "Google", "Amazon Prime", "Prime Video", "Planet Fitness", "LA Fitness",
}
STREAMING = {"Netflix", "Hulu", "Disney+", "Max", "YouTube", "Paramount+", "Peacock", "Prime Video", "Spotify"}
_SUB_CATEGORY = re.compile(r"subscri|streaming|membership|software|apps?\b|gym|fitness|music|news", re.I)
_BILL_CATEGORY = re.compile(r"utilit|electric|water|natural gas|gas & electric|internet|phone|insurance|mortgage|"
                            r"\brent\b|loan|payment|childcare|daycare|tuition|hoa|lessons|activities", re.I)


@dataclass
class Recurring:
    merchant: str
    category: str
    cadence: str
    interval_days: float
    typical_amount: float
    last_amount: float
    last_date: date
    first_date: date
    next_date: date
    count: int
    monthly_cost: float
    kind: str  # subscription | bill | giving | other
    active: bool
    is_new: bool
    amount_cv: float
    price_change: float = 0.0  # last − previous typical (only for stable-priced items)
    previous_amount: float = 0.0
    price_changed_on: date | None = None
    charge_ids: list[str] = field(default_factory=list)

    @property
    def annual_cost(self) -> float:
        return round(self.monthly_cost * 12, 2)

    @property
    def is_streaming(self) -> bool:
        return self.merchant in STREAMING


def _kind(merchant: str, category: str, cls: str, amount_cv: float, typical: float, n: int,
          strict: bool = False) -> str:
    if cls == CLASS_GIVING:
        return "giving"
    if merchant in KNOWN_SUBSCRIPTIONS or _SUB_CATEGORY.search(category):
        return "subscription"
    if cls == CLASS_DEBT or _BILL_CATEGORY.search(category):
        return "bill"
    # A fixed small charge on a regular schedule is almost always a subscription — but only trust
    # that guess for a merchant's whole history, not a slice of a store you shop at often.
    if not strict and cls == CLASS_DISCRETIONARY and amount_cv <= 0.05 and typical < 100 and n >= 3:
        return "subscription"
    return "other"


def _analyze(charges: list[Transaction], today: date, strict_amounts: bool) -> Recurring | None:
    charges = sorted(charges, key=lambda t: t.date)
    n = len(charges)
    if n < 2:
        return None
    amounts = [t.outflow for t in charges]
    if any(a <= 0 for a in amounts):
        return None
    gaps = [(b.date - a.date).days for a, b in zip(charges, charges[1:])]
    if sum(1 for g in gaps if g == 0) > 0:
        return None
    med_gap = median(gaps)
    cadence = next((c for c in CADENCES if c[2] <= med_gap <= c[3]), None)
    if cadence is None:
        return None
    name, nominal, lo, hi, per_month = cadence
    regular = sum(1 for g in gaps if lo <= g <= hi or 2 * lo <= g <= 2 * hi)
    if regular / len(gaps) < 0.75:
        return None

    t0 = charges[-1]
    merchant, category, cls = t0.merchant or t0.payee, t0.category, t0.cls
    var = cv(amounts)
    typical = round(median(amounts[-3:]) if var <= 0.1 else mean(amounts[-12:]), 2)
    if cls == CLASS_FEES:
        return None  # fees are reported separately
    kind = _kind(merchant, category, cls, var, typical, n, strict_amounts)
    if strict_amounts and (n < 3 or kind not in ("subscription", "bill", "giving")):
        return None  # amount-cluster matches need more evidence
    if n == 2 and kind not in ("subscription", "bill"):
        return None

    min_count = {"weekly": 4, "biweekly": 4, "monthly": 3}.get(name, 2)
    if n < min_count:
        # Two identical monthly charges from a known subscription is enough.
        if not (name == "monthly" and n == 2 and kind == "subscription" and var < 0.01):
            return None
    if strict_amounts and var > 0.05:
        return None
    if name in ("weekly", "biweekly") and var > 0.15 and kind != "bill":
        return None
    if var > 0.6 or (var > 0.25 and kind not in ("bill", "giving")):
        return None
    if cls == CLASS_DISCRETIONARY and kind == "other" and var > 0.1:
        return None

    days_since = (today - t0.date).days
    active = days_since <= nominal * 1.5 + 5
    if name in ("monthly", "quarterly", "semiannual", "annual"):
        step = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}[name]
        next_date = add_months(t0.date, step)
        while active and next_date < today:
            next_date = add_months(next_date, step)
    else:
        next_date = t0.date + timedelta(days=nominal)
        while active and next_date < today:
            next_date += timedelta(days=nominal)

    # Price change = a step from one stable price to a new one, within ~4 months.
    price_change, previous, changed_on = 0.0, 0.0, None
    if n >= 3:
        last = amounts[-1]
        k = 1
        while k < n and abs(amounts[-1 - k] - last) <= 0.01 * last + 0.005:
            k += 1
        before = amounts[: n - k][-3:]
        if before:
            previous = round(median(before), 2)
            delta = round(last - previous, 2)
            changed_on = charges[n - k].date
            if cv(before) <= 0.02 and abs(delta) >= max(0.5, 0.03 * previous) and (today - changed_on).days <= 120:
                price_change = delta

    return Recurring(
        merchant=merchant, category=category, cadence=name, interval_days=nominal,
        typical_amount=typical if not price_change else round(amounts[-1], 2),
        last_amount=round(amounts[-1], 2), last_date=t0.date, first_date=charges[0].date,
        next_date=next_date, count=n,
        monthly_cost=round((amounts[-1] if price_change else typical) * per_month, 2),
        kind=kind, active=active, is_new=(today - charges[0].date).days <= 100 and n <= 4,
        amount_cv=round(var, 3), price_change=price_change, previous_amount=previous,
        price_changed_on=changed_on if price_change else None,
        charge_ids=[t.id for t in charges],
    )


def _amount_clusters(charges: list[Transaction]) -> list[list[Transaction]]:
    ordered = sorted(charges, key=lambda t: t.outflow)
    clusters: list[list[Transaction]] = []
    for t in ordered:
        if clusters:
            ref = median([c.outflow for c in clusters[-1]])
            if abs(t.outflow - ref) <= max(1.0, 0.05 * ref):
                clusters[-1].append(t)
                continue
        clusters.append([t])
    return [c for c in clusters if len(c) >= 2]


def detect_recurring(expenses: list[Transaction], today: date) -> list[Recurring]:
    by_merchant: dict[str, list[Transaction]] = defaultdict(list)
    for t in expenses:
        if t.outflow > 0 and (t.merchant or t.payee):
            by_merchant[(t.merchant or t.payee).lower()].append(t)

    found: list[Recurring] = []
    for charges in by_merchant.values():
        if len(charges) < 2:
            continue
        whole = _analyze(charges, today, strict_amounts=False)
        if whole:
            found.append(whole)
            continue
        # One merchant, several fixed-price plans (e.g. two Apple subscriptions).
        for cluster in _amount_clusters(charges):
            r = _analyze(cluster, today, strict_amounts=True)
            if r:
                found.append(r)
    found.sort(key=lambda r: (-r.active, -r.monthly_cost))
    return found
