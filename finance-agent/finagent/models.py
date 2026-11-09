"""Core data types shared by every module."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

KIND_EXPENSE = "expense"
KIND_INCOME = "income"
KIND_TRANSFER = "transfer"

# Spending "classes" used to reason about a category, independent of what the
# budgeting app calls it.
CLASS_ESSENTIAL = "essential"
CLASS_DISCRETIONARY = "discretionary"
CLASS_GIVING = "giving"
CLASS_SAVINGS = "savings"
CLASS_DEBT = "debt"
CLASS_INCOME = "income"
CLASS_TRANSFER = "transfer"
CLASS_FEES = "fees"
CLASS_OTHER = "other"


@dataclass
class Transaction:
    """One money movement. ``amount`` is signed: negative = money out."""

    id: str
    date: date
    amount: float
    payee: str
    category: str = ""
    category_group: str = ""
    account: str = ""
    memo: str = ""
    source: str = ""
    is_transfer: bool = False
    pending: bool = False
    # Derived at load time by ``categorize.enrich`` (never stored), so that
    # rule changes apply retroactively to history.
    merchant: str = ""
    kind: str = ""
    cls: str = ""
    flags: set[str] = field(default_factory=set)

    @property
    def outflow(self) -> float:
        """Spend as a positive number (refunds come out negative)."""
        return -self.amount

    @property
    def month(self) -> str:
        return self.date.strftime("%Y-%m")


@dataclass
class Budget:
    """A planned monthly amount for one category."""

    month: str  # YYYY-MM
    category: str
    amount: float
    category_group: str = ""
