"""Small, dependency-free helpers: money, dates, months, ids."""

from __future__ import annotations

import calendar
import hashlib
import re
import statistics
from datetime import date, datetime, timedelta
from typing import Iterable, Sequence

# --------------------------------------------------------------------------- money

_AMOUNT_JUNK = re.compile(r"[^\d.\-]")


def parse_amount(value: object) -> float | None:
    """Parse "$1,234.56", "(12.34)", "-$5", "12.34-", "−7.00" → float. Blank → None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    s = s.replace("−", "-")  # unicode minus
    if s.endswith("-"):
        negative, s = True, s[:-1]
    if s.upper().endswith(" CR"):
        s = s[:-3]
    elif s.upper().endswith(" DR"):
        negative, s = True, s[:-3]
    cleaned = _AMOUNT_JUNK.sub("", s)
    if cleaned.count("-") > 0:
        negative = negative ^ True
        cleaned = cleaned.replace("-", "")
    if cleaned in ("", "."):
        return None
    try:
        amount = float(cleaned)
    except ValueError:
        return None
    return round(-amount if negative else amount, 2)


def money(value: float, symbol: str = "$", cents: bool = True, signed: bool = False) -> str:
    """Format money: 1234.5 → "$1,234.50" (or "$1,235" with cents=False)."""
    neg = value < -0.004
    v = abs(value)
    body = f"{v:,.2f}" if cents else f"{v:,.0f}"
    if neg:
        return f"-{symbol}{body}"
    return f"+{symbol}{body}" if signed and v >= 0.005 else f"{symbol}{body}"


def money_auto(value: float, symbol: str = "$") -> str:
    """Cents only where they matter: $17.99, $60, $1,226."""
    v = abs(value)
    return money(value, symbol, cents=v < 100 and abs(v - round(v)) > 0.004)


def pct(value: float | None, signed: bool = False, digits: int = 0) -> str:
    if value is None:
        return "—"
    s = f"{value * 100:+.{digits}f}%" if signed else f"{value * 100:.{digits}f}%"
    return s


# --------------------------------------------------------------------------- dates

_DATE_FORMATS_YMD = ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")
_DATE_FORMATS_MDY = ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y")
_DATE_FORMATS_DMY = ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y")
_DATE_FORMATS_TEXT = ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%b %d %Y")


def infer_date_order(samples: Iterable[str]) -> str:
    """Decide whether ambiguous slash dates are month-first ("mdy") or day-first ("dmy")."""
    for raw in samples:
        m = re.match(r"^\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", str(raw))
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 >= b:
            return "dmy"
        if b > 12 >= a:
            return "mdy"
    return "mdy"  # US default


def parse_date(value: object, order: str = "mdy") -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    s = s.split("T")[0] if re.match(r"^\d{4}-\d{2}-\d{2}T", s) else s
    formats = _DATE_FORMATS_YMD + (
        _DATE_FORMATS_DMY + _DATE_FORMATS_MDY if order == "dmy" else _DATE_FORMATS_MDY + _DATE_FORMATS_DMY
    ) + _DATE_FORMATS_TEXT
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def month_start(key: str) -> date:
    y, m = key.split("-")
    return date(int(y), int(m), 1)


def month_end(key: str) -> date:
    start = month_start(key)
    return date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])


def add_months(d: date, n: int) -> date:
    """Shift a date by n months, clamping the day (Jan 31 + 1 → Feb 28/29)."""
    idx = d.year * 12 + (d.month - 1) + n
    y, m = divmod(idx, 12)
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def shift_month(key: str, n: int) -> str:
    return month_key(add_months(month_start(key), n))


def complete_months(today: date, n: int) -> list[str]:
    """The n most recent fully-finished calendar months, oldest first."""
    current = month_key(today)
    return [shift_month(current, -i) for i in range(n, 0, -1)]


def month_label(key: str, short: bool = False) -> str:
    d = month_start(key)
    return d.strftime("%b" if short else "%B %Y")


def days_in_month(key: str) -> int:
    return month_end(key).day


def week_start(d: date, starts_on: int = 0) -> date:
    """Start of the week containing d. starts_on: 0=Monday … 6=Sunday."""
    return d - timedelta(days=(d.weekday() - starts_on) % 7)


def last_complete_week(today: date, starts_on: int = 0) -> tuple[date, date]:
    start = week_start(today, starts_on) - timedelta(days=7)
    return start, start + timedelta(days=6)


def fmt_date(d: date, with_year: bool = False) -> str:
    # Built by hand: "%-d" isn't portable (it fails on Windows).
    return f"{d:%b} {d.day}, {d.year}" if with_year else f"{d:%b} {d.day}"


def fmt_range(start: date, end: date) -> str:
    if start.year != end.year:
        return f"{fmt_date(start, True)} – {fmt_date(end, True)}"
    if start.month == end.month:
        return f"{start:%b} {start.day} – {end.day}, {end.year}"
    return f"{fmt_date(start)} – {fmt_date(end, True)}"


def fmt_datetime(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    return f"{dt:%b} {dt.day}, {dt.year} at {hour}:{dt:%M} {'AM' if dt.hour < 12 else 'PM'}"


# --------------------------------------------------------------------------- stats

def median(values: Sequence[float]) -> float:
    return statistics.median(values) if values else 0.0


def mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def cv(values: Sequence[float]) -> float:
    """Coefficient of variation (0 = identical amounts)."""
    if len(values) < 2:
        return 0.0
    m = statistics.fmean(values)
    return statistics.pstdev(values) / m if m else 0.0


# --------------------------------------------------------------------------- ids

def stable_id(*parts: object) -> str:
    h = hashlib.sha1("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return h[:20]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
