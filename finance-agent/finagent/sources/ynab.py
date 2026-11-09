"""YNAB (You Need A Budget) — official API, read-only.

Create a Personal Access Token at https://app.ynab.com/settings/developer and
export it as YNAB_TOKEN. The token never goes in config.toml.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Callable

from finagent.models import Budget, Transaction
from finagent.util import add_months, month_key

API_BASE = "https://api.ynab.com/v1"

# Payees YNAB uses for bookkeeping, not real income or spending.
_BOOKKEEPING_PAYEES = {"starting balance", "reconciliation balance adjustment", "manual balance adjustment"}
_INTERNAL_GROUPS = {"internal master category", "credit card payments", "hidden categories"}


class YNABError(RuntimeError):
    pass


def _default_http_get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error", {}).get("detail", "")
        except Exception:
            pass
        if e.code == 401:
            raise YNABError("YNAB rejected the token (401). Create a new Personal Access Token at "
                            "https://app.ynab.com/settings/developer and set YNAB_TOKEN.") from None
        if e.code == 404:
            raise YNABError(f"YNAB budget not found (404). Check source.budget_id in config.toml. {detail}") from None
        if e.code == 429:
            raise YNABError("YNAB rate limit reached (200 requests/hour). Try again later.") from None
        raise YNABError(f"YNAB API error {e.code}: {detail or e.reason}") from None
    except urllib.error.URLError as e:
        raise YNABError(f"Couldn't reach YNAB: {e.reason}") from None


class YNABSource:
    def __init__(self, token: str, budget_id: str = "last-used",
                 http_get: Callable[[str, str], dict] | None = None):
        if not token:
            raise YNABError("No YNAB token. Set the YNAB_TOKEN environment variable "
                            "(create one at https://app.ynab.com/settings/developer).")
        self.token = token
        self.budget_id = budget_id or "last-used"
        self._http_get = http_get or _default_http_get

    def _get(self, path: str, **params) -> dict:
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        url = f"{API_BASE}/budgets/{urllib.parse.quote(self.budget_id)}{path}{query}"
        return self._http_get(url, self.token)["data"]

    def list_budgets(self) -> list[dict]:
        data = self._http_get(f"{API_BASE}/budgets", self.token)["data"]
        return [{"id": b["id"], "name": b["name"], "last_modified_on": b.get("last_modified_on")}
                for b in data.get("budgets", [])]

    def fetch(self, since: date, today: date | None = None) -> tuple[list[Transaction], list[Budget]]:
        today = today or date.today()

        # Category id → (name, group)
        groups: dict[str, tuple[str, str]] = {}
        for g in self._get("/categories").get("category_groups", []):
            for c in g.get("categories", []):
                groups[c["id"]] = (c["name"], g["name"])

        # Only on-budget accounts: tracking accounts (investments, loan balances)
        # are net-worth bookkeeping, not cash flow.
        on_budget = {a["id"] for a in self._get("/accounts").get("accounts", [])
                     if a.get("on_budget") and not a.get("deleted")}

        txns: list[Transaction] = []
        for t in self._get("/transactions", since_date=since.isoformat()).get("transactions", []):
            if t.get("deleted") or t.get("account_id") not in on_budget:
                continue
            parent_payee = t.get("payee_name") or ""
            subs = [s for s in (t.get("subtransactions") or []) if not s.get("deleted")]
            parts = subs if subs else [t]
            for p in parts:
                cat_id = p.get("category_id")
                cat_name, grp_name = groups.get(cat_id, (p.get("category_name") or "", ""))
                payee = p.get("payee_name") or parent_payee
                has_real_category = bool(cat_name) and grp_name.lower() not in ("credit card payments",) \
                    and cat_name.lower() != "uncategorized"
                is_transfer = (bool(p.get("transfer_account_id")) and not has_real_category) \
                    or payee.lower() in _BOOKKEEPING_PAYEES
                txns.append(Transaction(
                    id=f"ynab-{p['id']}",
                    date=date.fromisoformat(t["date"]),
                    amount=round(p["amount"] / 1000.0, 2),
                    payee=payee,
                    category=cat_name or "",
                    category_group=grp_name,
                    account=t.get("account_name") or "",
                    memo=p.get("memo") or t.get("memo") or "",
                    source="ynab",
                    is_transfer=is_transfer,
                ))

        budgets: list[Budget] = []
        m = date(since.year, since.month, 1)
        while m <= today:
            month = self._get(f"/months/{m.isoformat()}").get("month", {})
            for c in month.get("categories", []):
                name, grp = groups.get(c.get("id"), (c.get("name", ""), c.get("category_group_name", "")))
                if c.get("deleted") or grp.lower() in _INTERNAL_GROUPS:
                    continue
                amount = (c.get("budgeted") or 0) / 1000.0
                if amount or not c.get("hidden"):
                    budgets.append(Budget(month=month_key(m), category=name, amount=round(amount, 2),
                                          category_group=grp))
            m = add_months(m, 1)
        return txns, budgets
