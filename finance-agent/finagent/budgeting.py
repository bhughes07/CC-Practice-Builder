"""Suggested budgets from real spending — shared by `budget-suggest` and the budget spreadsheet."""

from __future__ import annotations

import math
from dataclasses import dataclass

from finagent.insights import Analysis
from finagent.ledger import Ledger
from finagent.models import CLASS_DISCRETIONARY, CLASS_FEES, CLASS_GIVING, CLASS_SAVINGS, CLASS_TRANSFER


@dataclass
class Suggestion:
    group: str
    category: str
    target: float
    average: float
    kind: str  # fixed | flexible | essential | giving | sinking fund
    note: str = ""


def suggest(L: Ledger, A: Analysis, trim_pct: float = 10.0) -> list[Suggestion]:
    """Fixed bills at what they cost, lifestyle categories `trim_pct`% under average, essentials and giving at
    average, and one-off purchases moved into an 'Irregular Expenses' sinking fund."""
    trim = max(0.0, min(0.5, trim_pct / 100))
    window = A.window_expenses()
    out: list[Suggestion] = []
    for cat, row in A.cat_matrix_core.items():
        avg = sum(row.values()) / A.n
        cls = L.class_of(cat)
        if avg < 5 or cls in (CLASS_SAVINGS, CLASS_TRANSFER, CLASS_FEES):
            continue
        spent = [t for t in window if t.category == cat and t.id not in A.unusual_ids]
        total = sum(t.outflow for t in spent) or 1
        fixed = sum(t.outflow for t in spent if t.id in A.recurring_ids) / total >= 0.7
        if fixed:  # bills and subscriptions: budget what they cost
            out.append(Suggestion(L.group_of(cat), cat, math.ceil(avg / 5) * 5, avg,
                                  "giving" if cls == CLASS_GIVING else "fixed", "fixed"))
        elif cls == CLASS_DISCRETIONARY and not L.is_protected(cat):
            goal = avg * (1 - trim)
            step = 5 if goal < 100 else 25
            out.append(Suggestion(L.group_of(cat), cat, max(step, round(goal / step) * step), avg, "flexible",
                                  f"-{trim_pct:.0f}%"))
        else:
            step = 5 if avg < 100 else 25
            out.append(Suggestion(L.group_of(cat), cat, max(step, round(avg / step) * step), avg,
                                  "giving" if cls == CLASS_GIVING or L.is_protected(cat) else "essential"))
    one_offs = sum(t.outflow for t, _ in A.unusual) / A.n
    if one_offs >= 25:
        out.append(Suggestion("Planning", "Irregular Expenses", math.ceil(one_offs / 25) * 25, one_offs,
                              "sinking fund", "sinking fund"))
    out.sort(key=lambda s: (s.group, -s.average))
    return out
