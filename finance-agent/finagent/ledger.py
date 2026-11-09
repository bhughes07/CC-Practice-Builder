"""The Ledger: enriched transactions + budgets, with the aggregations every report needs."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable

from finagent.categorize import category_class, enrich
from finagent.models import (
    CLASS_GIVING, CLASS_SAVINGS, KIND_EXPENSE, KIND_INCOME, Budget, Transaction,
)
from finagent.util import complete_months, month_end, month_key, month_start


class Ledger:
    def __init__(self, txns: Iterable[Transaction], budgets: Iterable[Budget], config: dict, today: date):
        self.config = config
        self.today = today
        self.currency = config.get("household", {}).get("currency", "$")
        self.all = [t for t in enrich(txns, config) if t.date < today or t.date == today]
        self.expenses = [t for t in self.all if t.kind == KIND_EXPENSE]
        self.incomes = [t for t in self.all if t.kind == KIND_INCOME]

        self.groups: dict[str, str] = {}
        for t in self.all:
            if t.category and t.category_group and t.category not in self.groups:
                self.groups[t.category] = t.category_group

        # month → category → budgeted. Source budgets win; config budgets fill gaps.
        self.budget_map: dict[str, dict[str, float]] = defaultdict(dict)
        for b in budgets:
            if b.amount:
                self.budget_map[b.month][b.category] = b.amount
                if b.category_group:
                    self.groups.setdefault(b.category, b.category_group)
        cfg_budgets = {k: float(v) for k, v in (config.get("budgets") or {}).items()}
        if cfg_budgets and self.all:
            m = month_key(self.all[0].date)
            last = month_key(today)
            while m <= last:
                for cat, amt in cfg_budgets.items():
                    self.budget_map[m].setdefault(cat, amt)
                y, mo = map(int, m.split("-"))
                m = f"{y + (mo // 12)}-{(mo % 12) + 1:02d}"

        overrides = config.get("categories", {}).get("class_overrides", {}) or {}
        self.classes: dict[str, str] = {}
        for t in self.expenses:
            self.classes.setdefault(t.category, t.cls)
        for m in self.budget_map.values():
            for cat in m:
                self.classes.setdefault(cat, category_class(cat, self.groups.get(cat, ""), overrides))

        patterns = config.get("analysis", {}).get("protected", []) or []
        self._protected_rx = re.compile("|".join(re.escape(p) for p in patterns), re.I) if patterns else None

    # ------------------------------------------------------------------ coverage
    @property
    def first_date(self) -> date | None:
        return self.all[0].date if self.all else None

    @property
    def last_date(self) -> date | None:
        return self.all[-1].date if self.all else None

    def available_months(self, n: int) -> list[str]:
        """Up to n most recent *complete* months that the data actually covers."""
        if not self.all:
            return []
        months = complete_months(self.today, n)
        first = self.first_date
        return [m for m in months if month_start(m) + timedelta(days=4) >= first]

    def has_budgets(self) -> bool:
        return any(self.budget_map.values())

    # ------------------------------------------------------------------ filters
    def expenses_between(self, start: date, end: date) -> list[Transaction]:
        return [t for t in self.expenses if start <= t.date <= end]

    def incomes_between(self, start: date, end: date) -> list[Transaction]:
        return [t for t in self.incomes if start <= t.date <= end]

    def expenses_in_month(self, month: str) -> list[Transaction]:
        return self.expenses_between(month_start(month), month_end(month))

    # ------------------------------------------------------------------ totals
    def spend(self, start: date, end: date) -> float:
        return round(sum(t.outflow for t in self.expenses_between(start, end)), 2)

    def income(self, start: date, end: date) -> float:
        return round(sum(t.amount for t in self.incomes_between(start, end)), 2)

    def spend_month(self, month: str) -> float:
        return self.spend(month_start(month), month_end(month))

    def income_month(self, month: str) -> float:
        return self.income(month_start(month), month_end(month))

    def spend_by_category(self, start: date, end: date, txns: list[Transaction] | None = None) -> dict[str, float]:
        totals: dict[str, float] = defaultdict(float)
        for t in (txns if txns is not None else self.expenses_between(start, end)):
            totals[t.category or "Uncategorized"] += t.outflow
        return dict(sorted(((k, round(v, 2)) for k, v in totals.items()), key=lambda kv: -kv[1]))

    def category_by_month(self, months: list[str], exclude_ids: set[str] | None = None) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = defaultdict(lambda: {m: 0.0 for m in months})
        if not months:
            return {}
        start, end = month_start(months[0]), month_end(months[-1])
        for t in self.expenses_between(start, end):
            if exclude_ids and t.id in exclude_ids:
                continue
            out[t.category or "Uncategorized"][t.month] += t.outflow
        return {c: {m: round(v, 2) for m, v in row.items()} for c, row in out.items()}

    def merchant_totals(self, start: date, end: date) -> list[tuple[str, float, int]]:
        totals: dict[str, list] = defaultdict(lambda: [0.0, 0])
        for t in self.expenses_between(start, end):
            totals[t.merchant or t.payee][0] += t.outflow
            totals[t.merchant or t.payee][1] += 1
        return sorted(((m, round(v[0], 2), v[1]) for m, v in totals.items()), key=lambda x: -x[1])

    # ------------------------------------------------------------------ budgets
    def budget(self, month: str, category: str) -> float | None:
        return self.budget_map.get(month, {}).get(category)

    def budget_total(self, month: str) -> float | None:
        m = self.budget_map.get(month)
        if not m:
            return None
        return round(sum(v for c, v in m.items() if self.classes.get(c) != CLASS_SAVINGS), 2)

    # ------------------------------------------------------------------ classification
    def is_protected(self, category: str) -> bool:
        if self.classes.get(category) in (CLASS_GIVING, CLASS_SAVINGS):
            return True
        text = f"{self.groups.get(category, '')} {category}"
        return bool(self._protected_rx and self._protected_rx.search(text))

    def group_of(self, category: str) -> str:
        return self.groups.get(category, "") or "Other"

    def class_of(self, category: str) -> str:
        return self.classes.get(category, "other")
