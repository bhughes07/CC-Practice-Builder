"""Point detectors: bank fees, possible duplicate charges, unusually large transactions."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from finagent.models import CLASS_FEES, Transaction
from finagent.util import median

FEE_TYPES: list[tuple[str, str, re.Pattern]] = [
    ("overdraft", "Overdraft / NSF", re.compile(r"overdraft|\bod fee|\bnsf\b|insufficient funds|returned item", re.I)),
    ("late", "Late fee", re.compile(r"late (fee|charge|payment)", re.I)),
    ("interest", "Interest charge", re.compile(r"interest charge|finance charge|purchase interest|cash advance", re.I)),
    ("atm", "ATM fee", re.compile(r"atm.*(fee|surcharge)|(fee|surcharge).*\batm\b|non-?network atm", re.I)),
    ("foreign", "Foreign transaction fee", re.compile(
        r"foreign (transaction|exchange|txn)|intl (transaction|txn)|international (transaction|fee)|fx fee", re.I)),
    ("maintenance", "Account fee", re.compile(r"(monthly|maintenance|service) (fee|charge)|account fee", re.I)),
]

FEE_ADVICE = {
    "overdraft": "Turn on low-balance alerts and link savings for overdraft protection (or ask the bank to refund it "
                 "— first-time overdraft fees are often waived if you call).",
    "late": "Set every card to autopay at least the statement minimum, then call and ask for a one-time courtesy "
            "waiver.",
    "interest": "You're carrying a card balance. Pay it down before anything else discretionary; interest is the "
                "most expensive line in the budget.",
    "atm": "Use your bank's in-network ATMs or get cash back at the grocery checkout.",
    "foreign": "Use a no-foreign-transaction-fee card for travel and international online purchases.",
    "maintenance": "Ask the bank how to waive the monthly fee (direct deposit/minimum balance) or move to a "
                   "no-fee account.",
    "other": "Review this fee and ask the bank to waive it.",
}


@dataclass
class Fee:
    txn: Transaction
    type: str
    label: str


def fee_type(t: Transaction) -> tuple[str, str] | None:
    text = f"{t.payee} {t.memo} {t.category}"
    for key, label, rx in FEE_TYPES:
        if rx.search(text):
            return key, label
    if t.cls == CLASS_FEES:
        return "other", "Bank fee"
    return None


def find_fees(expenses: list[Transaction], start: date, end: date) -> list[Fee]:
    out = []
    for t in expenses:
        if start <= t.date <= end and t.outflow > 0:
            ft = fee_type(t)
            if ft:
                out.append(Fee(t, *ft))
                t.flags.add("fee")
    return out


def find_duplicates(expenses: list[Transaction], start: date, end: date) -> list[tuple[Transaction, Transaction]]:
    """Same merchant, same amount (to the cent), 0–3 days apart, and not a routine purchase."""
    lookback = start - timedelta(days=180)
    groups: dict[tuple[str, float], list[Transaction]] = defaultdict(list)
    for t in expenses:
        if lookback <= t.date <= end and t.outflow > 0:
            groups[((t.merchant or t.payee).lower(), round(t.outflow, 2))].append(t)
    pairs = []
    for (merchant, amount), txns in groups.items():
        if len(txns) < 2 or len(txns) > 4:  # >4 identical charges in 6 months = routine (same coffee order)
            continue
        txns.sort(key=lambda t: t.date)
        for a, b in zip(txns, txns[1:]):
            gap = (b.date - a.date).days
            if b.date < start or gap > 3:
                continue
            if amount >= 15 or (gap == 0 and amount >= 10):
                pairs.append((a, b))
                a.flags.add("possible duplicate")
                b.flags.add("possible duplicate")
    pairs.sort(key=lambda p: p[1].date)
    return pairs


def find_unusual(expenses: list[Transaction], start: date, end: date, floor: float = 150.0,
                 skip_ids: set[str] | None = None) -> list[tuple[Transaction, str]]:
    """Charges far above what you normally spend in that category."""
    by_cat: dict[str, list[Transaction]] = defaultdict(list)
    by_merchant: dict[str, list[Transaction]] = defaultdict(list)
    for t in expenses:
        by_cat[t.category].append(t)
        by_merchant[(t.merchant or t.payee).lower()].append(t)
    out = []
    for t in expenses:
        if not (start <= t.date <= end) or t.outflow < floor or (skip_ids and t.id in skip_ids):
            continue
        # Normal for this merchant (e.g. the monthly Costco run)? Then not unusual.
        same_merchant = [h for h in by_merchant[(t.merchant or t.payee).lower()]
                         if h.id != t.id and t.date - timedelta(days=180) <= h.date < t.date
                         and h.outflow >= 0.6 * t.outflow]
        if len(same_merchant) >= 2:
            continue
        history = [h.outflow for h in by_cat[t.category]
                   if h.id != t.id and t.date - timedelta(days=180) <= h.date <= t.date + timedelta(days=31)
                   and h.outflow > 0]
        if len(history) >= 3:
            typical = median(history)
            if typical > 0 and t.outflow >= 3 * typical:
                out.append((t, f"{t.outflow / typical:.1f}× your typical {t.category} charge"))
                t.flags.add("unusual")
        elif t.outflow >= 2 * floor:
            out.append((t, f"large charge in a category you rarely use ({t.category or 'Uncategorized'})"))
            t.flags.add("unusual")
    out.sort(key=lambda x: -x[0].outflow)
    return out
