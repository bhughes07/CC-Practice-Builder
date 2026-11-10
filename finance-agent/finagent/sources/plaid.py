"""Plaid: connect directly to your bank and credit-card accounts (read-only).

How it fits together
  1. You create a Plaid developer account and set PLAID_CLIENT_ID / PLAID_SECRET.
  2. `finagent plaid link` serves a small page on http://127.0.0.1 where you sign in to
     each bank through Plaid Link. Your bank password goes to Plaid, never to finagent.
  3. finagent exchanges the result for an access token and keeps it in data/plaid.json
     (owner-only permissions, git-ignored).
  4. `finagent sync` calls /transactions/sync with a saved cursor, so each sync only
     downloads what changed: new transactions, edits, and pending charges that posted.

Plaid reports money leaving an account as a positive amount; finagent flips the sign
so spending is negative like every other source.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from finagent.models import Transaction

BASE_URLS = {"sandbox": "https://sandbox.plaid.com", "production": "https://production.plaid.com"}
LINK_JS = "https://cdn.plaid.com/link/v2/stable/link-initialize.js"
SANDBOX_INSTITUTION = "ins_109508"  # "First Platypus Bank"
RELINK_CODES = {"ITEM_LOGIN_REQUIRED", "INVALID_CREDENTIALS", "INVALID_MFA", "ITEM_LOCKED",
                "PASSWORD_RESET_REQUIRED", "USER_SETUP_REQUIRED", "ITEM_NOT_FOUND", "ACCESS_NOT_GRANTED"}
SPENDING_ACCOUNT_TYPES = {"depository", "credit"}  # loans & investments are balances, not cash flow
P2P = {"Venmo", "Zelle", "Cash App", "PayPal", "Apple Cash"}


class PlaidError(RuntimeError):
    def __init__(self, code: str, message: str, display: str | None = None, error_type: str = ""):
        super().__init__(f"{code}: {display or message}")
        self.code, self.message, self.display, self.error_type = code, message, display, error_type

    @property
    def needs_relink(self) -> bool:
        return self.code in RELINK_CODES


Transport = Callable[[str, dict], dict]


def friendly(err: "PlaidError", env: str = "production") -> str:
    """Turn Plaid's key errors into a next step the user can take."""
    text = f"{err.message} {err.display or ''}".lower()
    which = "Sandbox" if env == "sandbox" else "Production"
    if err.code == "INVALID_FIELD" and ("secret" in text or "client_id" in text):
        part = "client ID" if "client_id" in text else f"{which} secret"
        return (f"Plaid says your {part} isn't in the right format. In Terminal, run "
                f"`python3 -m finagent keys --check` to see what's wrong with it.")
    if err.code == "INVALID_API_KEYS":
        return (f"Plaid didn't accept your client ID and {which} secret together. Copy both again from Plaid's "
                f"Keys page and save them with `python3 -m finagent keys --plaid`, then try again.")
    return err.display or err.message or err.code


class PlaidClient:
    """Minimal Plaid API client (standard library only)."""

    def __init__(self, client_id: str, secret: str, env: str = "production", transport: Transport | None = None):
        if env not in BASE_URLS:
            raise ValueError(f"plaid.env must be one of {sorted(BASE_URLS)} (got {env!r})")
        if not (client_id and secret) and transport is None:
            raise PlaidError("MISSING_KEYS", "Set the PLAID_CLIENT_ID and PLAID_SECRET environment variables "
                                             "(Plaid Dashboard → Keys). Use the secret for the environment in "
                                             f"config ({env}).")
        self.client_id, self.secret, self.env = client_id, secret, env
        self._transport = transport or self._http

    def _http(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            BASE_URLS[self.env] + path, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "PLAID-CLIENT-ID": self.client_id,
                     "PLAID-SECRET": self.secret})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode("utf-8"))
            except Exception:
                raise PlaidError(f"HTTP_{e.code}", str(e.reason)) from None
            raise PlaidError(err.get("error_code", f"HTTP_{e.code}"), err.get("error_message", ""),
                             err.get("display_message"), err.get("error_type", "")) from None
        except urllib.error.URLError as e:
            raise PlaidError("NETWORK", f"Couldn't reach Plaid: {e.reason}") from None

    def post(self, path: str, body: dict) -> dict:
        return self._transport(path, body)

    # ------------------------------------------------------------------ endpoints
    def create_link_token(self, client_user_id: str, client_name: str = "finagent", country_codes=("US",),
                          days_requested: int = 730, access_token: str | None = None) -> str:
        body: dict[str, Any] = {
            "client_name": client_name[:30], "language": "en", "country_codes": list(country_codes),
            "user": {"client_user_id": client_user_id},
        }
        if access_token:  # update mode: re-authenticate an existing connection
            body["access_token"] = access_token
        else:
            body["products"] = ["transactions"]
            body["transactions"] = {"days_requested": max(30, min(730, int(days_requested)))}
        return self.post("/link/token/create", body)["link_token"]

    def exchange_public_token(self, public_token: str) -> tuple[str, str]:
        r = self.post("/item/public_token/exchange", {"public_token": public_token})
        return r["access_token"], r["item_id"]

    def transactions_sync(self, access_token: str, cursor: str | None, count: int = 500) -> dict:
        body: dict[str, Any] = {"access_token": access_token, "count": count,
                                "options": {"include_original_description": True}}
        if cursor:
            body["cursor"] = cursor
        return self.post("/transactions/sync", body)

    def remove_item(self, access_token: str) -> None:
        self.post("/item/remove", {"access_token": access_token})

    def sandbox_public_token(self, institution_id: str = SANDBOX_INSTITUTION,
                             username: str = "user_transactions_dynamic", days_requested: int = 730) -> str:
        if self.env != "sandbox":
            raise PlaidError("NOT_SANDBOX", "Test connections only work with plaid.env = \"sandbox\".")
        r = self.post("/sandbox/public_token/create", {
            "institution_id": institution_id, "initial_products": ["transactions"],
            "options": {"override_username": username, "override_password": "pass_good",
                        "transactions": {"days_requested": days_requested}}})
        return r["public_token"]


# --------------------------------------------------------------------------- linked items on disk

class PlaidItems:
    """data/plaid.json: linked banks, their access tokens, and sync cursors. Owner-only permissions."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict[str, Any] = {"client_user_id": "", "items": []}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        if not self.data.get("client_user_id"):
            self.data["client_user_id"] = f"finagent-{uuid.uuid4().hex[:16]}"  # stable, contains no PII

    @property
    def items(self) -> list[dict]:
        return self.data.setdefault("items", [])

    @property
    def client_user_id(self) -> str:
        return self.data["client_user_id"]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:  # e.g. Windows
            pass

    def upsert(self, item: dict) -> None:
        self.data["items"] = [i for i in self.items if i["item_id"] != item["item_id"]] + [item]

    def find(self, query: str) -> list[dict]:
        q = query.lower()
        return [i for i in self.items if i["item_id"] == query or q in i.get("institution", {}).get("name", "").lower()]

    def remove(self, item_id: str) -> None:
        self.data["items"] = [i for i in self.items if i["item_id"] != item_id]


def new_item(access_token: str, item_id: str, env: str, metadata: dict | None) -> dict:
    metadata = metadata or {}
    inst = metadata.get("institution") or {}
    accounts = {
        a["id"]: {"name": a.get("name") or "", "mask": a.get("mask") or "", "type": a.get("type") or "",
                  "subtype": a.get("subtype") or ""}
        for a in (metadata.get("accounts") or []) if a.get("id")
    }
    return {"item_id": item_id, "access_token": access_token, "env": env,
            "institution": {"id": inst.get("institution_id", ""), "name": inst.get("name") or "Bank"},
            "accounts": accounts, "cursor": "", "status": "ok", "error": "",
            "linked_at": datetime.now().isoformat(timespec="seconds"), "last_sync": ""}


# --------------------------------------------------------------------------- categories

# Plaid personal finance categories (v1 and v2 detailed names) → (category, group).
# Anything not listed falls back to its primary in PRIMARY_MAP.
DETAILED_MAP: dict[str, tuple[str, str]] = {
    # Income
    "INCOME_WAGES": ("Paycheck", "Income"), "INCOME_SALARY": ("Paycheck", "Income"),
    "INCOME_DIVIDENDS": ("Investment Income", "Income"), "INCOME_INTEREST_EARNED": ("Interest Income", "Income"),
    "INCOME_TAX_REFUND": ("Tax Refund", "Income"), "INCOME_RETIREMENT_PENSION": ("Pension", "Income"),
    "INCOME_UNEMPLOYMENT": ("Unemployment", "Income"), "INCOME_CONTRACTOR": ("Side Income", "Income"),
    "INCOME_GIG_ECONOMY": ("Side Income", "Income"), "INCOME_RENTAL": ("Rental Income", "Income"),
    # Debt payments (credit-card payments are transfers; see _classify)
    "LOAN_PAYMENTS_CAR_PAYMENT": ("Car Payment", "Debt"),
    "LOAN_PAYMENTS_MORTGAGE_PAYMENT": ("Mortgage", "Housing"),
    "LOAN_PAYMENTS_STUDENT_LOAN_PAYMENT": ("Student Loan", "Debt"),
    "LOAN_PAYMENTS_PERSONAL_LOAN_PAYMENT": ("Personal Loan", "Debt"),
    "LOAN_PAYMENTS_BNPL": ("Buy Now Pay Later", "Debt"),
    # Bank fees (names are what the fee detector keys on)
    "BANK_FEES_ATM_FEES": ("ATM Fees", "Financial"), "BANK_FEES_OVERDRAFT_FEES": ("Overdraft Fees", "Financial"),
    "BANK_FEES_INSUFFICIENT_FUNDS": ("Insufficient Funds Fees", "Financial"),
    "BANK_FEES_INTEREST_CHARGE": ("Interest Charges", "Financial"),
    "BANK_FEES_FOREIGN_TRANSACTION_FEES": ("Foreign Transaction Fees", "Financial"),
    "BANK_FEES_LATE_FEES": ("Late Fees", "Financial"), "BANK_FEES_CASH_ADVANCE": ("Cash Advance Fees", "Financial"),
    # Housing & utilities
    "RENT_AND_UTILITIES_RENT": ("Rent", "Housing"),
    "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY": ("Gas & Electric", "Utilities"),
    "RENT_AND_UTILITIES_INTERNET_AND_CABLE": ("Internet & Cable", "Utilities"),
    "RENT_AND_UTILITIES_TELEPHONE": ("Phone", "Utilities"), "RENT_AND_UTILITIES_WATER": ("Water", "Utilities"),
    "RENT_AND_UTILITIES_SEWAGE_AND_WASTE_MANAGEMENT": ("Trash & Sewer", "Utilities"),
    "HOME_IMPROVEMENT_FURNITURE": ("Furniture & Decor", "Housing"),
    "HOME_IMPROVEMENT_SECURITY": ("Home Security", "Housing"),
    # Food
    "FOOD_AND_DRINK_GROCERIES": ("Groceries", "Food"), "FOOD_AND_DRINK_COFFEE": ("Coffee Shops", "Food"),
    "FOOD_AND_DRINK_BEER_WINE_AND_LIQUOR": ("Alcohol & Bars", "Food"),
    # Entertainment
    "ENTERTAINMENT_TV_AND_MOVIES": ("Streaming & Movies", "Entertainment"),
    "ENTERTAINMENT_MUSIC_AND_AUDIO": ("Music & Audio", "Entertainment"),
    "ENTERTAINMENT_VIDEO_GAMES": ("Video Games", "Entertainment"),
    "ENTERTAINMENT_CASINOS_AND_GAMBLING": ("Gambling", "Entertainment"),
    # Shopping
    "GENERAL_MERCHANDISE_CLOTHING_AND_ACCESSORIES": ("Clothing", "Shopping"),
    "GENERAL_MERCHANDISE_GIFTS_AND_NOVELTIES": ("Gifts", "Shopping"),
    "GENERAL_MERCHANDISE_PET_SUPPLIES": ("Pets", "Shopping"),
    "GENERAL_MERCHANDISE_ELECTRONICS": ("Electronics", "Shopping"),
    "GENERAL_MERCHANDISE_SPORTING_GOODS": ("Sporting Goods", "Shopping"),
    "GENERAL_MERCHANDISE_BOOKSTORES_AND_NEWSSTANDS": ("Books", "Shopping"),
    "GENERAL_MERCHANDISE_OFFICE_SUPPLIES": ("Office Supplies", "Shopping"),
    "GENERAL_MERCHANDISE_CONVENIENCE_STORES": ("Convenience Stores", "Shopping"),
    "GENERAL_MERCHANDISE_TOBACCO_AND_VAPE": ("Tobacco & Vape", "Shopping"),
    # Services
    "GENERAL_SERVICES_AUTOMOTIVE": ("Auto Maintenance", "Transportation"),
    "GENERAL_SERVICES_CHILDCARE": ("Childcare", "Family"), "GENERAL_SERVICES_EDUCATION": ("Education", "Family"),
    "GENERAL_SERVICES_INSURANCE": ("Insurance", "Insurance"),
    "GENERAL_SERVICES_ACCOUNTING_AND_FINANCIAL_PLANNING": ("Financial Services", "Services"),
    "GENERAL_SERVICES_CONSULTING_AND_LEGAL": ("Professional Services", "Services"),
    "GENERAL_SERVICES_POSTAGE_AND_SHIPPING": ("Shipping", "Services"),
    "GENERAL_SERVICES_STORAGE": ("Storage", "Services"),
    # Personal care & medical
    "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS": ("Gym", "Personal Care"),
    "PERSONAL_CARE_HAIR_AND_BEAUTY": ("Hair & Beauty", "Personal Care"),
    "PERSONAL_CARE_LAUNDRY_AND_DRY_CLEANING": ("Laundry & Dry Cleaning", "Personal Care"),
    "MEDICAL_PHARMACIES_AND_SUPPLEMENTS": ("Pharmacy", "Medical"), "MEDICAL_DENTAL_CARE": ("Dental", "Medical"),
    "MEDICAL_EYE_CARE": ("Eye Care", "Medical"), "MEDICAL_VETERINARY_SERVICES": ("Vet", "Pets"),
    # Transportation & travel
    "TRANSPORTATION_GAS": ("Gas & Fuel", "Transportation"), "TRANSPORTATION_PARKING": ("Parking", "Transportation"),
    "TRANSPORTATION_PUBLIC_TRANSIT": ("Public Transit", "Transportation"),
    "TRANSPORTATION_TAXIS_AND_RIDE_SHARES": ("Rideshare & Taxis", "Transportation"),
    "TRANSPORTATION_TOLLS": ("Tolls", "Transportation"),
    # Giving & government
    "GOVERNMENT_AND_NON_PROFIT_DONATIONS": ("Giving", "Giving"),
    "GOVERNMENT_AND_NON_PROFIT_TAX_PAYMENT": ("Taxes", "Government"),
}
PRIMARY_MAP: dict[str, tuple[str, str]] = {
    "INCOME": ("Other Income", "Income"), "LOAN_PAYMENTS": ("Loan Payment", "Debt"),
    "BANK_FEES": ("Bank Fees", "Financial"), "ENTERTAINMENT": ("Entertainment", "Entertainment"),
    "FOOD_AND_DRINK": ("Dining Out", "Food"), "GENERAL_MERCHANDISE": ("Shopping", "Shopping"),
    "HOME_IMPROVEMENT": ("Home Maintenance", "Housing"), "MEDICAL": ("Medical", "Medical"),
    "PERSONAL_CARE": ("Personal Care", "Personal Care"), "GENERAL_SERVICES": ("Services", "Services"),
    "GOVERNMENT_AND_NON_PROFIT": ("Government Services", "Government"),
    "TRANSPORTATION": ("Transportation", "Transportation"), "TRAVEL": ("Travel", "Travel"),
    "RENT_AND_UTILITIES": ("Utilities", "Utilities"),
}


def _classify(pfc: dict | None, merchant: str, amount: float) -> tuple[str, str, bool]:
    """Return (category, group, is_transfer). `amount` is already in finagent's sign (negative = out)."""
    primary = (pfc or {}).get("primary") or ""
    detailed = (pfc or {}).get("detailed") or ""
    if primary in ("TRANSFER_IN", "TRANSFER_OUT"):
        if amount < 0 and ("FROM_APPS" in detailed or merchant in P2P):
            return "Payments to People", "Other", False  # paying the babysitter by Venmo is spending
        if detailed.endswith("WITHDRAWAL"):
            return "Cash & ATM", "Other", False
        return "Transfer", "Transfers", True
    if primary == "LOAN_DISBURSEMENTS":
        return "Transfer", "Transfers", True  # borrowed money isn't income
    if detailed.endswith("CREDIT_CARD_PAYMENT"):
        return "Credit Card Payment", "Transfers", True
    if detailed in DETAILED_MAP:
        return (*DETAILED_MAP[detailed], False)
    if primary in PRIMARY_MAP:
        return (*PRIMARY_MAP[primary], False)
    return "Uncategorized", "", False  # finagent guesses from the merchant name


def to_transaction(t: dict, item: dict) -> Transaction | None:
    acct = item.get("accounts", {}).get(t.get("account_id"), {})
    if acct.get("type") and acct["type"] not in SPENDING_ACCOUNT_TYPES:
        return None
    amount = round(-float(t["amount"]), 2)
    merchant = t.get("merchant_name") or ""
    payee = merchant or t.get("name") or t.get("original_description") or "Unknown"
    category, group, is_transfer = _classify(t.get("personal_finance_category"), merchant, amount)
    raw = t.get("original_description") or t.get("name") or ""
    acct_label = " ".join(x for x in (acct.get("name", ""), f"••{acct['mask']}" if acct.get("mask") else "") if x)
    return Transaction(
        id=f"plaid:{item['item_id']}:{t['transaction_id']}",
        date=date.fromisoformat(t.get("authorized_date") or t["date"]),
        amount=amount, payee=payee, category=category, category_group=group,
        account=acct_label or item.get("institution", {}).get("name", ""),
        memo=raw if raw and raw != payee else "", source="plaid", is_transfer=is_transfer,
        pending=bool(t.get("pending")),
    )


# --------------------------------------------------------------------------- sync

@dataclass
class SyncResult:
    institution: str
    added: int = 0
    modified: int = 0
    removed: int = 0
    status: str = ""
    error: str = ""
    needs_relink: bool = False
    skipped_accounts: set[str] = field(default_factory=set)

    def line(self) -> str:
        if self.error:
            return f"  {self.institution}: {self.error}"
        s = f"  {self.institution}: {self.added} new, {self.modified} updated, {self.removed} removed"
        if self.status == "NOT_READY" and not (self.added or self.modified):
            s += " — Plaid is still downloading your history; run `finagent sync` again in a few minutes"
        return s


def sync_item(client: PlaidClient, item: dict, store) -> SyncResult:
    res = SyncResult(item.get("institution", {}).get("name", "Bank"))
    start_cursor = item.get("cursor") or None
    for attempt in range(3):
        cursor, added, modified, removed, accounts = start_cursor, [], [], [], {}
        status = ""
        try:
            while True:
                page = client.transactions_sync(item["access_token"], cursor)
                added += page.get("added", [])
                modified += page.get("modified", [])
                removed += page.get("removed", [])
                for a in page.get("accounts", []) or []:
                    accounts[a["account_id"]] = a
                status = page.get("transactions_update_status", "")
                cursor = page.get("next_cursor") or cursor
                if not page.get("has_more"):
                    break
            break
        except PlaidError as err:
            # Data changed mid-pagination: restart the whole loop from the cursor we started with.
            if err.code == "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION" and attempt < 2:
                continue
            item["status"] = "relink" if err.needs_relink else "error"
            item["error"] = err.code
            res.needs_relink = err.needs_relink
            res.error = (f"needs you to sign in again — run `finagent plaid link --update "
                         f"\"{res.institution}\"`" if err.needs_relink else f"sync failed ({err})")
            return res

    for acc_id, a in accounts.items():
        known = item.setdefault("accounts", {}).setdefault(acc_id, {})
        known.update({"name": a.get("name") or known.get("name", ""), "mask": a.get("mask") or known.get("mask", ""),
                      "type": a.get("type") or known.get("type", ""), "subtype": a.get("subtype") or known.get("subtype", "")})

    store.delete_transactions(f"plaid:{item['item_id']}:{r['transaction_id']}" for r in removed)
    mapped = [x for x in (to_transaction(t, item) for t in added + modified) if x is not None]
    ins, upd = store.upsert_transactions(mapped)
    res.added, res.modified, res.removed, res.status = ins, upd, len(removed), status
    if cursor:
        item["cursor"] = cursor
    item.update(status="ok", error="", last_sync=datetime.now().isoformat(timespec="seconds"))
    return res


def items_path(cfg) -> Path:
    default = "data/plaid-sandbox.json" if cfg.plaid_env == "sandbox" else "data/plaid.json"
    return cfg.resolve(cfg["plaid"].get("items_file", default))


def make_client(cfg, transport: Transport | None = None) -> PlaidClient:
    client_id, secret = cfg.plaid_keys()
    return PlaidClient(client_id or "", secret or "", cfg.plaid_env, transport)


def sync_all(cfg, store, client: PlaidClient | None = None) -> str:
    items = PlaidItems(items_path(cfg))
    if not items.items:
        return "Plaid: no banks connected yet. Run `finagent plaid link` to connect one."
    try:
        client = client or make_client(cfg)
    except PlaidError as err:
        return f"Plaid: {err.message}"
    lines = ["Plaid:"]
    for item in items.items:
        if item.get("env", "production") != client.env:
            lines.append(f"  {item['institution']['name']}: skipped (linked in {item.get('env')}, "
                         f"config is {client.env})")
            continue
        lines.append(sync_item(client, item, store).line())
    items.save()
    return "\n".join(lines)


# --------------------------------------------------------------------------- the local Link page

LINK_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · finagent</title>
<script src="__LINK_JS__"></script>
<style>
  :root { --ink:#18212b; --muted:#5d6b79; --line:#e3e7ec; --bg:#f3f5f7; --accent:#1d6b57; --bad:#b42318; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  main { max-width:560px; margin:48px auto; padding:0 16px; }
  .card { background:#fff; border:1px solid var(--line); border-radius:14px; padding:28px; }
  .bar { height:4px; width:56px; background:var(--accent); border-radius:2px; margin-bottom:16px; }
  h1 { font-size:24px; margin:0 0 8px; }
  p { color:var(--muted); margin:0 0 16px; }
  button { font:inherit; font-weight:600; border:0; border-radius:10px; padding:12px 18px; cursor:pointer; }
  #go { background:var(--accent); color:#fff; }
  #go:disabled { opacity:.5; cursor:wait; }
  #finish { background:#fff; color:var(--ink); border:1px solid var(--line); margin-left:8px; }
  ul { list-style:none; padding:0; margin:18px 0 0; }
  li { padding:10px 12px; border:1px solid var(--line); border-radius:10px; margin-bottom:8px; }
  li small { color:var(--muted); display:block; }
  #msg { margin-top:16px; min-height:24px; }
  .bad { color:var(--bad); }
</style></head>
<body><main><div class="card">
  <div class="bar"></div>
  <h1>__TITLE__</h1>
  <p>You'll sign in through Plaid's secure window. finagent never sees your bank password and only gets
     read-only access to transactions. Everything stays on this computer.</p>
  <button id="go" disabled>Loading Plaid…</button><button id="finish">I'm done</button>
  <ul id="done"></ul>
  <div id="msg" role="status"></div>
</div></main>
<script>
const BASE = location.pathname.replace(/\\/$/, "");
const MODE = "__MODE__";
const go = document.getElementById("go"), msgEl = document.getElementById("msg");
let handler = null;
function msg(text, bad) { msgEl.textContent = text || ""; msgEl.className = bad ? "bad" : ""; }
async function post(path, body) {
  const r = await fetch(BASE + path, { method: "POST", headers: { "Content-Type": "application/json" },
                                       body: JSON.stringify(body || {}) });
  return r.json();
}
async function prepare() {
  go.disabled = true; go.textContent = "Loading Plaid…";
  if (typeof Plaid === "undefined") {
    msg("Couldn't load Plaid's secure window from cdn.plaid.com. Check your internet connection or pause any " +
        "ad/script blocker for this page, then reload.", true);
    go.textContent = "Reload"; go.disabled = false; go.onclick = () => location.reload(); return;
  }
  const t = await post("/token");
  if (!t.link_token) { msg(t.error || "Couldn't start Plaid.", true); go.textContent = "Try again"; go.disabled = false; return; }
  handler = Plaid.create({
    token: t.link_token,
    onSuccess: async (public_token, metadata) => {
      msg("Saving the connection…");
      const out = await post("/exchange", { public_token, metadata });
      if (!out.ok) { msg(out.error || "Couldn't save the connection.", true); return; }
      const li = document.createElement("li");
      li.innerHTML = "<strong></strong><small></small>";
      li.querySelector("strong").textContent = "✓ " + out.institution;
      li.querySelector("small").textContent = out.accounts.join(" · ");
      document.getElementById("done").appendChild(li);
      if (MODE === "update") { msg("Reconnected. You can close this tab."); finish(); return; }
      msg("Connected! Add another bank or credit card, or click I'm done.");
      prepare();
    },
    onExit: (err) => {
      if (err) msg(err.display_message || err.error_message || "Plaid closed with an error.", true);
      go.disabled = false;
    },
  });
  go.textContent = MODE === "update" ? "Reconnect" : (document.querySelectorAll("#done li").length ? "Connect another account" : "Connect a bank");
  go.disabled = false;
}
async function finish() {
  await post("/done");
  go.disabled = true; document.getElementById("finish").disabled = true;
  msg("All set — you can close this tab and go back to the terminal.");
}
go.onclick = () => { if (handler) { go.disabled = true; handler.open(); } else prepare(); };
document.getElementById("finish").onclick = finish;
prepare();
</script></body></html>
"""


class LinkSession:
    """A one-time local web page for Plaid Link, bound to 127.0.0.1 and guarded by a random path."""

    def __init__(self, client: PlaidClient, items: PlaidItems, client_name: str = "finagent",
                 country_codes=("US",), days_requested: int = 730, port: int = 8765,
                 update_item: dict | None = None):
        self.client, self.items, self.update_item = client, items, update_item
        self.client_name, self.country_codes, self.days_requested = client_name, country_codes, days_requested
        self.nonce = secrets.token_urlsafe(18)
        self.linked: list[dict] = []
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/{self.nonce}/"
        self._thread: threading.Thread | None = None

    # -------------------------------------------------------------- request handling
    def _page(self) -> bytes:
        title = (f"Reconnect {self.update_item['institution']['name']}" if self.update_item
                 else "Connect your bank")
        html = (LINK_PAGE.replace("__TITLE__", title).replace("__LINK_JS__", LINK_JS)
                .replace("__MODE__", "update" if self.update_item else "link"))
        return html.encode("utf-8")

    def _token(self) -> dict:
        try:
            token = self.client.create_link_token(
                self.items.client_user_id, self.client_name, self.country_codes, self.days_requested,
                access_token=self.update_item["access_token"] if self.update_item else None)
            return {"link_token": token}
        except PlaidError as err:
            return {"error": friendly(err, self.client.env)}

    def _exchange(self, body: dict) -> dict:
        metadata = body.get("metadata") or {}
        try:
            with self.lock:
                if self.update_item:
                    item = self.update_item
                    item.update(status="ok", error="")
                else:
                    access_token, item_id = self.client.exchange_public_token(body["public_token"])
                    item = new_item(access_token, item_id, self.client.env, metadata)
                self.items.upsert(item)
                self.items.save()
                self.linked.append(item)
            names = [" ".join(x for x in (a["name"], f"••{a['mask']}" if a["mask"] else "") if x)
                     for a in item["accounts"].values()]
            return {"ok": True, "institution": item["institution"]["name"], "accounts": names}
        except (PlaidError, KeyError) as err:
            return {"ok": False, "error": f"Couldn't save the connection: {err}"}

    def _handler(self):
        session = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep the terminal quiet
                pass

            def _allowed(self) -> bool:
                host = self.headers.get("Host", "")
                return host in (f"127.0.0.1:{session.port}", f"localhost:{session.port}")

            def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not self._allowed():
                    return self._send(403, b'{"error":"forbidden"}')
                if self.path.rstrip("/") == f"/{session.nonce}":
                    return self._send(200, session._page(), "text/html; charset=utf-8")
                self._send(404, b'{"error":"not found"}')

            def do_POST(self):
                if not self._allowed() or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    return self._send(403, b'{"error":"forbidden"}')
                base = f"/{session.nonce}"
                length = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    return self._send(400, b'{"error":"bad json"}')
                if self.path == f"{base}/token":
                    out = session._token()
                elif self.path == f"{base}/exchange":
                    out = session._exchange(body)
                elif self.path == f"{base}/done":
                    session.done.set()
                    out = {"ok": True}
                else:
                    return self._send(404, b'{"error":"not found"}')
                self._send(200, json.dumps(out).encode("utf-8"))

        return Handler

    # -------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def wait(self, timeout: float = 1800) -> None:
        deadline = time.monotonic() + timeout
        while not self.done.is_set() and time.monotonic() < deadline:
            self.done.wait(0.5)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def run(self, open_browser: bool = True, timeout: float = 1800) -> list[dict]:
        self.start()
        try:
            if open_browser:
                webbrowser.open(self.url)
            self.wait(timeout)
        finally:
            self.stop()
        return self.linked
