"""CSV import with auto-detection for common budgeting-app and bank exports.

Supported out of the box (auto-detected from the header row):
  ynab      YNAB register export (Outflow / Inflow columns)
  monarch   Monarch Money transactions export
  mint      Mint / Credit Karma export (Transaction Type = debit|credit)
  rocket    Rocket Money export
  copilot   Copilot Money export
  generic   Anything else with a date, a description, and either an Amount
            column or Debit/Credit columns (most banks, Tiller, Simplifi,
            EveryDollar-style sheets)
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from finagent.models import Transaction
from finagent.util import infer_date_order, parse_amount, parse_date, stable_id

SYNONYMS = {
    "date": ["date", "transaction date", "trans date", "posted date", "post date", "posting date",
             "trans. date", "booking date", "original date"],
    "payee": ["payee", "merchant", "description", "name", "merchant name", "custom name", "details",
              "transaction description", "description 1", "payee name", "original statement",
              "original description"],
    "amount": ["amount", "transaction amount", "amt", "value", "amount (usd)", "net amount"],
    "debit": ["debit", "debits", "withdrawal", "withdrawals", "money out", "outflow", "debit amount",
              "charge", "charges", "paid out"],
    "credit": ["credit", "credits", "deposit", "deposits", "money in", "inflow", "credit amount", "paid in"],
    "category": ["category", "categories", "budget category", "category name", "sub category", "subcategory"],
    "group": ["category group", "parent category", "master category", "group"],
    "account": ["account", "account name", "account type"],
    "memo": ["memo", "notes", "note", "comments"],
}


@dataclass
class ImportResult:
    path: str
    format: str
    transactions: list[Transaction]
    skipped: int
    sign_flipped: bool


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").replace("﻿", "").strip().strip('"').lower())


def detect_format(headers: list[str]) -> str:
    h = {_norm(x) for x in headers}
    if {"outflow", "inflow", "payee"} <= h:
        return "ynab"
    if {"merchant", "original statement", "amount"} <= h:
        return "monarch"
    if {"transaction type", "original description", "amount"} <= h:
        return "mint"
    if ({"custom name", "institution name"} <= h) or ({"ignored from", "tax deductible"} <= h):
        return "rocket"
    if ({"parent category", "excluded"} <= h) or ("account mask" in h):
        return "copilot"
    return "generic"


def _find_header_row(rows: list[list[str]]) -> int:
    """Banks often prepend account info lines; find the real header row."""
    date_names = set(SYNONYMS["date"])
    money_names = set(SYNONYMS["amount"] + SYNONYMS["debit"] + SYNONYMS["credit"])
    for i, row in enumerate(rows[:30]):
        cells = {_norm(c) for c in row}
        if cells & date_names and cells & money_names:
            return i
    return 0


def _col(headers: list[str], key: str) -> int | None:
    normalized = [_norm(h) for h in headers]
    for name in SYNONYMS[key]:
        if name in normalized:
            return normalized.index(name)
    return None


def _get(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


_INCOME_CAT = re.compile(r"income|paycheck|salary|payroll|wages|deposit", re.I)
_TRANSFER_CAT = re.compile(r"transfer|credit card payment|^payment$|internal", re.I)


def _infer_expenses_positive(amounts: list[float], categories: list[str]) -> bool:
    """Is this a file where spending is written as a positive number?"""
    income = [a for a, c in zip(amounts, categories) if _INCOME_CAT.search(c or "")]
    if len(income) >= 2:
        return sum(1 for a in income if a < 0) > len(income) / 2
    nonzero = [a for a in amounts if a]
    if not nonzero:
        return False
    return sum(1 for a in nonzero if a > 0) / len(nonzero) > 0.6


def read_csv(path: str | Path, fmt: str = "auto", sign: str = "auto", date_order: str = "auto",
             account: str | None = None) -> ImportResult:
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    return parse_csv_text(text, source_name=Path(path).name, fmt=fmt, sign=sign,
                          date_order=date_order, account=account)


def parse_csv_text(text: str, source_name: str = "import.csv", fmt: str = "auto", sign: str = "auto",
                   date_order: str = "auto", account: str | None = None) -> ImportResult:
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if not rows:
        return ImportResult(source_name, fmt, [], 0, False)
    hdr_idx = _find_header_row(rows)
    headers, body = rows[hdr_idx], rows[hdr_idx + 1:]
    fmt = detect_format(headers) if fmt == "auto" else fmt

    i_date = _col(headers, "date")
    if i_date is None:
        raise ValueError(f"{source_name}: couldn't find a date column in headers {headers}")
    order = infer_date_order(_get(r, i_date) for r in body) if date_order == "auto" else date_order

    parser = {"ynab": _parse_ynab, "monarch": _parse_monarch, "mint": _parse_mint,
              "rocket": _parse_rocket, "copilot": _parse_copilot}.get(fmt, _parse_generic)
    parsed, skipped = parser(headers, body, order, sign)

    # Spending must be negative internally. Some apps export it positive.
    flipped = False
    single_amount_column = fmt in ("rocket", "copilot") or (fmt == "generic" and _col(headers, "amount") is not None)
    if single_amount_column and parsed:
        if sign == "expenses_positive" or (
            sign == "auto" and _infer_expenses_positive([p["amount"] for p in parsed],
                                                        [p["category"] for p in parsed])):
            flipped = True
            for p in parsed:
                p["amount"] = -p["amount"]

    # Stable ids: identical rows in one file get an occurrence index, so
    # re-importing the same (or an overlapping) export never double-counts.
    seen: Counter = Counter()
    txns: list[Transaction] = []
    for p in parsed:
        acct = account or p["account"]
        key = (p["date"].isoformat(), f"{p['amount']:.2f}", p["payee"].lower(), acct.lower())
        occurrence = seen[key]
        seen[key] += 1
        txns.append(Transaction(
            id="csv-" + stable_id(*key, occurrence),
            date=p["date"], amount=p["amount"], payee=p["payee"], category=p["category"],
            category_group=p["group"], account=acct, memo=p["memo"], source=f"csv:{fmt}",
            is_transfer=p["transfer"], pending=p.get("pending", False),
        ))
    return ImportResult(source_name, fmt, txns, skipped, flipped)


# --------------------------------------------------------------------------- parsers
# Each returns (list of dicts, skipped_count). Amount sign: negative = out,
# except formats flagged for sign inference above.

def _row(date, amount, payee, category="", group="", account="", memo="", transfer=False, pending=False):
    return {"date": date, "amount": amount, "payee": payee, "category": category, "group": group,
            "account": account, "memo": memo, "transfer": transfer, "pending": pending}


def _parse_ynab(headers, body, order, sign):
    ix = {k: _col(headers, k) for k in SYNONYMS}
    normalized = [_norm(h) for h in headers]
    i_combo = normalized.index("category group/category") if "category group/category" in normalized else None
    i_cat = normalized.index("category") if "category" in normalized else None
    i_grp = normalized.index("category group") if "category group" in normalized else None
    out, skipped = [], 0
    for r in body:
        d = parse_date(_get(r, ix["date"]), order)
        outflow = parse_amount(_get(r, ix["debit"])) or 0.0
        inflow = parse_amount(_get(r, ix["credit"])) or 0.0
        if d is None or (not outflow and not inflow):
            skipped += 1
            continue
        cat, grp = _get(r, i_cat), _get(r, i_grp)
        if not cat and i_combo is not None:
            combo = _get(r, i_combo)
            grp, _, cat = combo.partition(": ") if ": " in combo else ("", "", combo)
        payee = _get(r, ix["payee"])
        # "Transfer : Savings" — a transfer unless YNAB required a category
        # (budget → tracking account, e.g. a mortgage payment), which is spending.
        transfer = payee.lower().startswith("transfer :") and not cat
        out.append(_row(d, round(inflow - abs(outflow), 2), payee, cat, grp,
                        _get(r, ix["account"]), _get(r, ix["memo"]), transfer))
    return out, skipped


def _parse_monarch(headers, body, order, sign):
    ix = {k: _col(headers, k) for k in SYNONYMS}
    i_merchant = [_norm(h) for h in headers].index("merchant")
    out, skipped = [], 0
    for r in body:
        d, amt = parse_date(_get(r, ix["date"]), order), parse_amount(_get(r, ix["amount"]))
        if d is None or amt is None:
            skipped += 1
            continue
        cat = _get(r, ix["category"])
        out.append(_row(d, amt, _get(r, i_merchant), cat, "", _get(r, ix["account"]), _get(r, ix["memo"]),
                        bool(_TRANSFER_CAT.search(cat))))
    return out, skipped


def _parse_mint(headers, body, order, sign):
    normalized = [_norm(h) for h in headers]
    ix = {k: _col(headers, k) for k in SYNONYMS}
    i_desc = normalized.index("description")
    i_type = normalized.index("transaction type")
    out, skipped = [], 0
    for r in body:
        d, amt = parse_date(_get(r, ix["date"]), order), parse_amount(_get(r, ix["amount"]))
        if d is None or amt is None:
            skipped += 1
            continue
        amt = -abs(amt) if _get(r, i_type).lower() == "debit" else abs(amt)
        cat = _get(r, ix["category"])
        out.append(_row(d, amt, _get(r, i_desc), cat, "", _get(r, ix["account"]), _get(r, ix["memo"]),
                        bool(_TRANSFER_CAT.search(cat))))
    return out, skipped


def _parse_rocket(headers, body, order, sign):
    normalized = [_norm(h) for h in headers]
    ix = {k: _col(headers, k) for k in SYNONYMS}
    i_name = normalized.index("name") if "name" in normalized else ix["payee"]
    i_custom = normalized.index("custom name") if "custom name" in normalized else None
    i_ignored = normalized.index("ignored from") if "ignored from" in normalized else None
    out, skipped = [], 0
    for r in body:
        d, amt = parse_date(_get(r, ix["date"]), order), parse_amount(_get(r, ix["amount"]))
        if d is None or amt is None:
            skipped += 1
            continue
        cat = _get(r, ix["category"])
        ignored = bool(_get(r, i_ignored))
        out.append(_row(d, amt, _get(r, i_custom) or _get(r, i_name), cat, "", _get(r, ix["account"]),
                        _get(r, ix["memo"]), ignored or bool(_TRANSFER_CAT.search(cat))))
    return out, skipped


def _parse_copilot(headers, body, order, sign):
    normalized = [_norm(h) for h in headers]
    ix = {k: _col(headers, k) for k in SYNONYMS}
    i_type = normalized.index("type") if "type" in normalized else None
    i_excl = normalized.index("excluded") if "excluded" in normalized else None
    i_status = normalized.index("status") if "status" in normalized else None
    out, skipped = [], 0
    for r in body:
        d, amt = parse_date(_get(r, ix["date"]), order), parse_amount(_get(r, ix["amount"]))
        if d is None or amt is None:
            skipped += 1
            continue
        ttype = _get(r, i_type).lower()
        cat = _get(r, ix["category"])
        if ttype == "income" and not cat:
            cat = "Income"
        excluded = _get(r, i_excl).lower() in ("true", "yes", "1")
        out.append(_row(d, amt, _get(r, ix["payee"]), cat, _get(r, ix["group"]), _get(r, ix["account"]),
                        _get(r, ix["memo"]), excluded or "transfer" in ttype or bool(_TRANSFER_CAT.search(cat)),
                        pending=_get(r, i_status).lower() == "pending"))
    return out, skipped


def _parse_generic(headers, body, order, sign):
    ix = {k: _col(headers, k) for k in SYNONYMS}
    if ix["amount"] is None and ix["debit"] is None and ix["credit"] is None:
        raise ValueError(f"Couldn't find an amount (or debit/credit) column in headers {headers}")
    out, skipped = [], 0
    for r in body:
        d = parse_date(_get(r, ix["date"]), order)
        if ix["amount"] is not None:
            amt = parse_amount(_get(r, ix["amount"]))
        else:
            debit = parse_amount(_get(r, ix["debit"])) or 0.0
            credit = parse_amount(_get(r, ix["credit"])) or 0.0
            amt = round(abs(credit) - abs(debit), 2) if (debit or credit) else None
        if d is None or amt is None:
            skipped += 1
            continue
        cat = _get(r, ix["category"])
        out.append(_row(d, amt, _get(r, ix["payee"]), cat, _get(r, ix["group"]), _get(r, ix["account"]),
                        _get(r, ix["memo"]), bool(_TRANSFER_CAT.search(cat))))
    return out, skipped
