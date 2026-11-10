"""Merchant clean-up, category classes, and income/expense/transfer classification."""

from __future__ import annotations

import re
from typing import Iterable

from finagent.models import (
    CLASS_DEBT, CLASS_DISCRETIONARY, CLASS_ESSENTIAL, CLASS_FEES, CLASS_GIVING, CLASS_INCOME,
    CLASS_OTHER, CLASS_SAVINGS, CLASS_TRANSFER, KIND_EXPENSE, KIND_INCOME, KIND_TRANSFER, Transaction,
)

# --------------------------------------------------------------------------- merchants

# Order matters: specific patterns before general ones (Uber Eats before Uber).
MERCHANT_ALIASES: list[tuple[str, str]] = [
    (r"amazon prime|amzn prime|prime membership", "Amazon Prime"),
    (r"prime video", "Prime Video"),
    (r"\bamzn\b|amazon|amzn mktp", "Amazon"),
    (r"netflix", "Netflix"),
    (r"spotify", "Spotify"),
    (r"\bhulu\b", "Hulu"),
    (r"disney ?\+|disneyplus|disney plus", "Disney+"),
    (r"\bmax\.com\b|hbo ?max|\bhbo\b", "Max"),
    (r"youtube", "YouTube"),
    (r"paramount", "Paramount+"),
    (r"peacock", "Peacock"),
    (r"audible", "Audible"),
    (r"icloud", "iCloud"),
    (r"apple\.com|itunes|apple services|apple bill", "Apple"),
    (r"google ?play|google \*|google one", "Google"),
    (r"uber ?\*? ?eats|ubereats", "Uber Eats"),
    (r"doordash|\bdd \*", "DoorDash"),
    (r"grubhub", "Grubhub"),
    (r"instacart", "Instacart"),
    (r"\buber\b", "Uber"),
    (r"\blyft\b", "Lyft"),
    (r"starbucks", "Starbucks"),
    (r"dutch bros", "Dutch Bros"),
    (r"dunkin", "Dunkin'"),
    (r"chick-?fil-?a", "Chick-fil-A"),
    (r"mcdonald", "McDonald's"),
    (r"whataburger", "Whataburger"),
    (r"taco bell", "Taco Bell"),
    (r"chipotle", "Chipotle"),
    (r"sonic drive|\bsonic\b", "Sonic"),
    (r"raising cane", "Raising Cane's"),
    (r"panera", "Panera Bread"),
    (r"wal-?mart|wm supercenter|wal mart", "Walmart"),
    (r"\btarget\b", "Target"),
    (r"costco", "Costco"),
    (r"sam'?s ?club", "Sam's Club"),
    (r"kroger", "Kroger"),
    (r"\bh-?e-?b\b", "H-E-B"),
    (r"tom thumb", "Tom Thumb"),
    (r"\baldi\b", "Aldi"),
    (r"trader joe", "Trader Joe's"),
    (r"whole ?foods|wholefds", "Whole Foods"),
    (r"publix", "Publix"),
    (r"safeway", "Safeway"),
    (r"albertsons", "Albertsons"),
    (r"sprouts", "Sprouts"),
    (r"buc-?ee", "Buc-ee's"),
    (r"quiktrip|\bqt\s*\d", "QuikTrip"),
    (r"racetrac", "RaceTrac"),
    (r"7-?eleven", "7-Eleven"),
    (r"murphy ?usa", "Murphy USA"),
    (r"shell oil|\bshell\b", "Shell"),
    (r"exxon", "Exxon"),
    (r"chevron", "Chevron"),
    (r"home depot", "The Home Depot"),
    (r"lowe'?s", "Lowe's"),
    (r"best buy", "Best Buy"),
    (r"\bcvs\b", "CVS"),
    (r"walgreens", "Walgreens"),
    (r"venmo", "Venmo"),
    (r"zelle", "Zelle"),
    (r"cash app|square cash", "Cash App"),
    (r"paypal", "PayPal"),
    (r"at&t|\batt\b", "AT&T"),
    (r"verizon", "Verizon"),
    (r"t-?mobile", "T-Mobile"),
    (r"comcast|xfinity", "Xfinity"),
    (r"spectrum", "Spectrum"),
    (r"planet fitness", "Planet Fitness"),
    (r"la fitness", "LA Fitness"),
    (r"geico", "GEICO"),
    (r"state farm", "State Farm"),
    (r"progressive", "Progressive"),
    (r"allstate", "Allstate"),
    (r"txu energy|\btxu\b", "TXU Energy"),
]
_ALIAS_RE = [(re.compile(p, re.I), name) for p, name in MERCHANT_ALIASES]

_PREFIX_RE = re.compile(
    r"^(?:sq ?\*|tst ?\* ?|pp ?\*|paypal ?\*|sp ?\* ?|py ?\*|in ?\*|cke ?\*|bt ?\*|fs ?\*|"
    r"pos (?:debit |purchase )?|debit card purchase[ -]*|dbt crd \d* ?|checkcard \d* ?|"
    r"purchase authorized on \d\d/\d\d |recurring payment (?:authorized on \d\d/\d\d )?|"
    r"ach (?:debit|withdrawal) |visa purchase |card purchase |electronic payment )+",
    re.I,
)
_STATE_SUFFIX = re.compile(
    r"\s+(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|"
    r"NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)$"
)
_SMALL_WORDS = {"of", "and", "the", "at", "for", "in", "on", "a", "to", "by"}
_KEEP_UPPER = {"ATM", "USA", "BBQ", "LLC", "II", "III", "IV", "TV", "DFW", "YMCA", "UPS", "USPS", "IRS", "ACH"}


def _titlecase(s: str) -> str:
    words = []
    for i, w in enumerate(s.split()):
        if w.upper() in _KEEP_UPPER:
            words.append(w.upper())
        elif i > 0 and w.lower() in _SMALL_WORDS:
            words.append(w.lower())
        else:
            words.append("-".join(part[:1].upper() + part[1:].lower() for part in w.split("-")))
    return " ".join(words)


def normalize_merchant(payee: str) -> str:
    """'SQ *BLUE BOTTLE COFFEE #123 DALLAS TX' → 'Blue Bottle Coffee'; 'AMZN Mktp US*2K4' → 'Amazon'."""
    raw = (payee or "").strip()
    if not raw:
        return ""
    for rx, name in _ALIAS_RE:
        if rx.search(raw):
            return name
    s = _PREFIX_RE.sub("", raw)
    s = re.sub(r"\s+\d{2}/\d{2}(?:/\d{2,4})?\b.*$", "", s)  # trailing dates
    s = re.sub(r"\*\s*[A-Z0-9]{4,}\b", "", s)  # reference codes after '*'
    s = re.sub(r"#\s*\d+", "", s)  # store numbers
    s = re.sub(r"\b\d{4,}\b", "", s)  # long digit runs
    s = re.sub(r"\s+\d{2,}$", "", s.strip())  # trailing store numbers
    s = re.sub(r"\b(?:www\.)?([a-z0-9-]+)\.(?:com|net|org|io|co)\b", r"\1", s, flags=re.I)
    s = re.sub(r"[*_]+", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" -.,")
    if s.isupper() or s.islower():
        if s.isupper() and len(s.split()) >= 3:
            s = _STATE_SUFFIX.sub("", s)
        s = _titlecase(s)
    return s.strip() or raw


# --------------------------------------------------------------------------- category classes

_CLASS_RULES: list[tuple[str, re.Pattern]] = [
    (CLASS_TRANSFER, re.compile(r"transfer|credit card payment|cc payment|internal master", re.I)),
    (CLASS_GIVING, re.compile(r"tith|giving|charit|donat|church|offering|mission|generosity|ministr", re.I)),
    (CLASS_SAVINGS, re.compile(r"saving|invest|retire|401k|\bira\b|emergency fund|brokerage|529|college fund|"
                               r"irregular|sinking fund", re.I)),
    (CLASS_INCOME, re.compile(r"income|paycheck|salary|payroll|wages|ready to assign|to be budgeted|"
                              r"inflow|dividend|interest earned|bonus", re.I)),
    (CLASS_FEES, re.compile(r"\bfees?\b|bank charge|service charge|finance charge|interest charge", re.I)),
    (CLASS_DEBT, re.compile(r"debt|loan|car payment|auto payment|student|credit card interest", re.I)),
    (CLASS_ESSENTIAL, re.compile(
        r"rent|mortgage|housing|utilit|electric|power|water|sewer|trash|natural gas|gas & electric|"
        r"internet|phone|cell|mobile|insurance|medical|health|doctor|pharmacy|prescription|dental|vision|"
        r"childcare|daycare|tuition|school|education|grocer|food at home|supermarket|transport|fuel|"
        r"\bgas\b|auto|car maint|vehicle|property tax|hoa|\btax|home maint|home repair|repairs", re.I)),
    (CLASS_DISCRETIONARY, re.compile(
        r"dining|restaurant|eating out|takeout|take-out|delivery|coffee|fast food|entertainment|\bfun\b|"
        r"shopping|clothing|apparel|amazon|hobb|travel|vacation|subscription|streaming|gift|alcohol|bar|"
        r"recreation|sport|game|book|electronic|decor|personal care|beauty|hair|gym|fitness|kids|toys|"
        r"activities|pets?\b|movies|music|household|drink|software|gambl|convenience|tobacco|vape|lifestyle|"
        r"personal|misc|birthday|babysit|cleaning|lawn|car wash|haircut|christmas|xmas|holiday", re.I)),
]


def category_class(category: str, group: str = "", overrides: dict | None = None) -> str:
    if overrides:
        for key in (category, f"{group}: {category}"):
            if key in overrides:
                return overrides[key]
    text = f"{group} {category}".strip()
    if not text:
        return CLASS_OTHER
    # An explicit Income / Transfers group wins ("Investment Income" is income, not savings).
    g = group.strip().lower()
    if g in ("income", "inflow"):
        return CLASS_INCOME
    if g in ("transfers", "transfer"):
        return CLASS_TRANSFER
    # Category name is more specific than the group: check it first.
    for target in (category, text):
        if not target:
            continue
        for cls, rx in _CLASS_RULES:
            if rx.search(target):
                return cls
    return CLASS_OTHER


# --------------------------------------------------------------------------- uncategorized guesses

_MERCHANT_CATEGORY: dict[str, str] = {
    **{m: "Groceries" for m in ("Kroger", "H-E-B", "Tom Thumb", "Aldi", "Trader Joe's", "Whole Foods",
                                 "Publix", "Safeway", "Albertsons", "Sprouts", "Costco", "Sam's Club", "Instacart")},
    **{m: "Dining Out" for m in ("Chick-fil-A", "McDonald's", "Whataburger", "Taco Bell", "Chipotle", "Sonic",
                                  "Raising Cane's", "Panera Bread", "DoorDash", "Uber Eats", "Grubhub")},
    **{m: "Coffee Shops" for m in ("Starbucks", "Dutch Bros", "Dunkin'")},
    **{m: "Gas & Fuel" for m in ("Shell", "Exxon", "Chevron", "QuikTrip", "RaceTrac", "7-Eleven",
                                  "Murphy USA", "Buc-ee's")},
    **{m: "Subscriptions" for m in ("Netflix", "Spotify", "Hulu", "Disney+", "Max", "YouTube", "Paramount+",
                                     "Peacock", "Audible", "iCloud", "Apple", "Google", "Amazon Prime",
                                     "Prime Video")},
    **{m: "Shopping" for m in ("Amazon", "Target", "Walmart", "Best Buy")},
    **{m: "Home Improvement" for m in ("The Home Depot", "Lowe's")},
    **{m: "Medical" for m in ("CVS", "Walgreens")},
    **{m: "Phone & Internet" for m in ("AT&T", "Verizon", "T-Mobile", "Xfinity", "Spectrum")},
    **{m: "Insurance" for m in ("GEICO", "State Farm", "Progressive", "Allstate")},
    **{m: "Utilities" for m in ("TXU Energy",)},
    **{m: "Gym" for m in ("Planet Fitness", "LA Fitness")},
    **{m: "Transportation" for m in ("Uber", "Lyft")},
}
_KEYWORD_CATEGORY: list[tuple[re.Pattern, str]] = [
    (re.compile(r"payroll|direct dep|dir dep|salary", re.I), "Income"),
    (re.compile(r"overdraft|nsf|\bfee\b|late charge|finance charge|interest charge", re.I), "Bank Fees"),
    (re.compile(r"church|ministr|tithe|donation", re.I), "Giving"),
    (re.compile(r"restaurant|grill|cafe|pizza|bbq|taco|burger|sushi|kitchen|diner|bistro|wings", re.I), "Dining Out"),
    (re.compile(r"coffee|espresso", re.I), "Coffee Shops"),
    (re.compile(r"fuel|petro|gas station", re.I), "Gas & Fuel"),
    (re.compile(r"pharmacy|clinic|medical|dental|hospital|pediatric|urgent care|health", re.I), "Medical"),
    (re.compile(r"insurance", re.I), "Insurance"),
    (re.compile(r"electric|energy|water|utility|utilities|atmos|oncor|reliant", re.I), "Utilities"),
    (re.compile(r"mortgage|rent\b|apartments?", re.I), "Housing"),
    (re.compile(r"market|grocery|foods\b", re.I), "Groceries"),
]

UNCATEGORIZED = {"", "uncategorized", "uncategorised", "none", "n/a", "other", "unknown"}


def guess_category(merchant: str, payee: str) -> str:
    if merchant in _MERCHANT_CATEGORY:
        return _MERCHANT_CATEGORY[merchant]
    for rx, cat in _KEYWORD_CATEGORY:
        if rx.search(payee) or rx.search(merchant):
            return cat
    return "Uncategorized"


# --------------------------------------------------------------------------- kind

_TRANSFER_PAYEE = re.compile(
    r"^transfer\b|transfer (to|from)\b|online transfer|credit (card|crd) (payment|autopay|epay)|"
    r"payment thank you|autopay payment|card payment - thank|mobile payment - thank|ach pmt.*card", re.I)
_INCOME_PAYEE = re.compile(r"payroll|direct dep|dir dep|salary|paycheck", re.I)


def classify_kind(t: Transaction, cls: str) -> str:
    if t.is_transfer or cls == CLASS_TRANSFER:
        return KIND_TRANSFER
    if cls == CLASS_SAVINGS:
        return KIND_TRANSFER  # moving money to/from savings is neither spending nor income
    if cls == CLASS_INCOME:
        return KIND_INCOME if t.amount > 0 else KIND_EXPENSE
    if t.category.strip().lower() in UNCATEGORIZED:
        if _TRANSFER_PAYEE.search(t.payee):
            return KIND_TRANSFER
        if t.amount > 0 and (_INCOME_PAYEE.search(t.payee) or t.amount >= 400):
            return KIND_INCOME
    return KIND_EXPENSE


_AMOUNT_TERM = re.compile(r"^(.*?)\s*\$\s*([\d,]+(?:\.\d{1,2})?)\s*$")


def compile_line_rules(rules: list[dict] | None) -> list[tuple[re.Pattern, str, float | None, str, str]]:
    """[{"line", "group", "terms"}] → [(word-boundary pattern, term, exact amount or None, line, group)].
    A term may end with an amount — "Apple $2.99" — to tell apart several charges from one merchant."""
    out = []
    for r in rules or []:
        for term in r.get("terms", []):
            term, amount = term.strip(), None
            m = _AMOUNT_TERM.match(term)
            if m and m.group(1).strip():
                term, amount = m.group(1).strip(), float(m.group(2).replace(",", ""))
            if term:
                out.append((re.compile(r"(?<![\w])" + re.escape(term) + r"(?![\w])", re.I), term.lower(), amount,
                            r["line"], r.get("group", "")))
    return out


def match_line(t: Transaction, compiled) -> tuple[str, str] | None:
    """Your budget line for a transaction. Most specific wins: merchant + exact amount, then a term found in the
    merchant / description, then a term naming the bank's category; ties go to the longest term."""
    text = f"{t.merchant} {t.payee} {t.memo}"
    best = None
    for rx, term, amount, line, group in compiled:
        if amount is not None:
            if not (rx.search(text) and abs(abs(t.amount) - amount) < 0.005):
                continue
            score = (3, len(term))
        elif rx.search(text):
            score = (2, len(term))
        elif term == t.category.strip().lower():
            score = (1, len(term))
        else:
            continue
        if best is None or score > best[0]:
            best = (score, line, group)
    return (best[1], best[2]) if best else None


CARD_PAYMENT = re.compile(
    r"credit card payment|credit (card|crd) (payment|autopay|epay|pymt|pmt)|card ?(payment|pmt)|"
    r"autopay payment|ach pmt.*card|payment to .*card|"
    r"\b(visa|mastercard|amex|american express|discover|citi|capital one|synchrony|barclays?)\b.*"
    r"\b(payment|pmt|autopay)\b", re.I)
NOT_CARD = re.compile(r"mortgage|loan|auto pay(ment)? to|car pay|student|insurance", re.I)
CARD_PAYMENTS_LINE = "Credit Card Payments"


def _count_card_payments(txns: list[Transaction], mode: str) -> None:
    """Decide which credit-card payments are spending.

    auto     — a payment counts as spending ("Credit Card Payments") unless the money shows up on another
               connected account (same amount, within a week): then that card's purchases are already counted
               one by one and counting the payment too would count the same dollars twice.
    spending — every card payment counts (cash-flow view: what leaves checking).
    transfer — never count card payments (purchases view).
    """
    def is_card_payment(t: Transaction) -> bool:
        if t.amount >= 0 or NOT_CARD.search(f"{t.payee} {t.category}"):
            return False
        if CARD_PAYMENT.search(t.category):
            return True
        # Only trust the description when the source didn't already file it as something else.
        loose = t.category.strip().lower() in UNCATEGORIZED or t.is_transfer or "transfer" in t.category.lower()
        return loose and bool(CARD_PAYMENT.search(t.payee))

    outs = [t for t in txns if is_card_payment(t)]
    if mode == "transfer":
        for o in outs:
            o.is_transfer = True
        return
    used: set[str] = set()
    for o in sorted(outs, key=lambda t: t.date):
        if mode == "auto":
            match = next((i for i in txns if i.amount > 0 and i.id not in used and i.account != o.account
                          and abs(i.amount + o.amount) < 0.01 and abs((i.date - o.date).days) <= 7
                          and not PAYCHECK_HINT.search(f"{i.payee} {i.category}")), None)
            if match:
                used.add(match.id)
                continue
        o.is_transfer = False
        o.category, o.category_group = CARD_PAYMENTS_LINE, "Debt"
        o.flags.add("card payment")


PAYCHECK_HINT = re.compile(r"payroll|paycheck|salary|wages|direct dep", re.I)


def enrich(txns: Iterable[Transaction], config: dict | None = None) -> list[Transaction]:
    """Fill merchant / category guesses / class / kind. Returns a new sorted list."""
    cats = (config or {}).get("categories", {}) if config else {}
    rules: dict[str, str] = cats.get("merchant_rules", {}) or {}
    force = bool(cats.get("force_merchant_rules", False))
    overrides: dict[str, str] = cats.get("class_overrides", {}) or {}
    ignore = {c.lower() for c in (cats.get("ignore", []) or [])}
    lines = compile_line_rules(cats.get("line_rules"))
    txns = list(txns)
    _count_card_payments(txns, str(cats.get("card_payments", "auto")).lower())
    out: list[Transaction] = []
    for t in txns:
        if t.category.lower() in ignore:
            continue
        t.merchant = normalize_merchant(t.payee)
        if t.merchant in rules and (force or t.category.strip().lower() in UNCATEGORIZED):
            t.category = rules[t.merchant]
        elif t.category.strip().lower() in UNCATEGORIZED and not t.is_transfer:
            guess = guess_category(t.merchant, t.payee)
            if guess != "Uncategorized" or not t.category:
                t.category = guess
        t.cls = category_class(t.category, t.category_group, overrides)
        if "card payment" in t.flags:
            t.cls = CLASS_DEBT  # money leaving checking for a card that isn't connected: real spending
        # Your own budget lines (from the spreadsheet's Matches column) re-file spending under your names.
        if lines and not t.is_transfer and t.cls not in (CLASS_INCOME, CLASS_TRANSFER):
            hit = match_line(t, lines)
            if hit:
                t.category, t.category_group = hit[0], hit[1] or t.category_group
                t.cls = category_class(t.category, t.category_group, overrides)
        t.kind = classify_kind(t, t.cls)
        if t.kind == KIND_INCOME:
            t.cls = CLASS_INCOME
        out.append(t)
    out.sort(key=lambda x: (x.date, x.id))
    return out
