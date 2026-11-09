import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finagent.config import load_config  # noqa: E402
from finagent.insights import Analysis  # noqa: E402
from finagent.ledger import Ledger  # noqa: E402
from finagent.models import Transaction  # noqa: E402
from finagent.sources.demo import generate  # noqa: E402

TODAY = date(2026, 11, 9)


@pytest.fixture
def cfg():
    return load_config(overrides={"household": {"name": "Test Household"}})


@pytest.fixture
def demo(cfg):
    txns, budgets = generate(TODAY)
    ledger = Ledger(txns, budgets, cfg, TODAY)
    return ledger, Analysis(ledger, ledger.available_months(6))


def tx(d, amount, payee, category="", group="", account="Card", **kw) -> Transaction:
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return Transaction(id=kw.pop("id", f"{d}-{payee}-{amount}-{kw.get('n', 0)}"), date=d, amount=amount, payee=payee,
                       category=category, category_group=group, account=account,
                       **{k: v for k, v in kw.items() if k != "n"})
