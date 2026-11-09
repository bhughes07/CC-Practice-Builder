"""Build weekly, monthly, and multi-month review reports."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from finagent.detectors import find_duplicates, find_fees, find_unusual
from finagent.insights import Analysis, Insight
from finagent.ledger import Ledger
from finagent.models import CLASS_DEBT, CLASS_GIVING, CLASS_SAVINGS, Transaction
from finagent.reports.model import Report
from finagent.util import (
    days_in_month, fmt_date, fmt_range, mean, money_auto, month_end, month_key, month_label, month_start, shift_month,
)

CLASS_LABELS = {
    "essential": "Essentials", "discretionary": "Lifestyle & wants", "giving": "Giving", "debt": "Debt payments",
    "fees": "Fees", "other": "Other", "savings": "Savings", "income": "Income", "transfer": "Transfers",
}


def _money(cur: str):
    return lambda v: money_auto(v, cur)


def _cell(text: str, tone: str = "") -> dict:
    return {"text": text, "tone": tone}


def _delta_text(m, current: float, reference: float | None, label: str, lower_is_good: bool = True) -> tuple[str, str]:
    if not reference:
        return "", ""
    diff = current - reference
    if abs(diff) < 0.5:
        return f"same as {label}", ""
    direction = "more" if diff > 0 else "less"
    good = (diff < 0) == lower_is_good
    return f"{m(abs(diff))} {direction} than {label}", ("good" if good else "bad")


# --------------------------------------------------------------------------- shared sections

def itemization_section(ledger: Ledger, txns: list[Transaction], title: str, note: str = "") -> dict:
    groups: dict[str, dict[str, list[Transaction]]] = defaultdict(lambda: defaultdict(list))
    for t in txns:
        groups[ledger.group_of(t.category)][t.category or "Uncategorized"].append(t)
    out = []
    for g, cats in groups.items():
        cat_list = []
        for c, ts in cats.items():
            ts.sort(key=lambda t: (t.date, t.merchant))
            cat_list.append({"name": c, "total": round(sum(t.outflow for t in ts), 2), "count": len(ts),
                             "transactions": ts})
        cat_list.sort(key=lambda x: -x["total"])
        out.append({"name": g, "total": round(sum(c["total"] for c in cat_list), 2), "categories": cat_list})
    out.sort(key=lambda x: -x["total"])
    return {"type": "itemization", "title": title, "note": note, "groups": out,
            "total": round(sum(t.outflow for t in txns), 2), "count": len(txns)}


def recurring_table(A: Analysis, m, title: str = "Subscriptions & recurring bills") -> dict:
    rows = []
    active = sorted(A.active_recurring, key=lambda r: (-r.monthly_cost))
    for r in active:
        notes = []
        if r.is_new:
            notes.append(_cell("new", "warn"))
        if r.price_change > 0:
            notes.append(_cell(f"↑ from {m(r.previous_amount)}", "bad"))
        if r.amount_cv > 0.1:
            notes.append(_cell("varies", ""))
        rows.append([r.merchant, r.kind.capitalize(), r.cadence, m(r.last_amount), m(r.monthly_cost),
                     fmt_date(r.next_date), notes])
    total = sum(r.monthly_cost for r in active)
    subs = sum(r.monthly_cost for r in active if r.kind == "subscription")
    return {"type": "table", "title": title,
            "columns": ["Merchant", "Type", "Cadence", "Last charge", "Per month", "Next", "Notes"],
            "align": ["l", "l", "l", "r", "r", "l", "l"], "rows": rows,
            "note": f"{len(active)} active · {m(total)}/mo committed ({m(total * 12)}/yr) · "
                    f"subscriptions alone {m(subs)}/mo."}


def columns_section(A: Analysis, title: str = "Income vs. spending") -> dict:
    return {"type": "columns", "title": title, "series": ["Income", "Spending"],
            "points": [{"label": x["label"], "values": [x["income"], x["spend"]]} for x in A.monthly]}


def class_bars(A: Analysis, m, title: str = "Spending mix (monthly average)") -> dict:
    split = A.class_split()
    total = sum(v for v in split.values() if v > 0) or 1
    rows = [{"label": CLASS_LABELS.get(k, k.title()), "value": v, "display": m(v),
             "sub": f"{v / total * 100:.0f}%", "tone": "accent" if k == "discretionary" else ""}
            for k, v in split.items() if v > 0]
    return {"type": "bars", "title": title, "rows": rows}


# --------------------------------------------------------------------------- weekly

def build_weekly(ledger: Ledger, ws: date, we: date, A: Analysis | None) -> Report:
    cur = ledger.currency
    m = _money(cur)
    recurring_ids = A.recurring_ids if A else set()
    unusual_ctx = A.unusual_ids if A else set()

    def flexible(t: Transaction) -> bool:
        return t.id not in recurring_ids and t.cls not in (CLASS_GIVING, CLASS_DEBT, CLASS_SAVINGS)

    week = ledger.expenses_between(ws, we)
    total = round(sum(t.outflow for t in week), 2)
    flex = round(sum(t.outflow for t in week if flexible(t)), 2)
    income_week = ledger.income(ws, we)

    # Typical week = average of up to 8 prior weeks of flexible spending.
    prev_weeks, prev_cat = [], defaultdict(list)
    for k in range(1, 9):
        s, e = ws - timedelta(days=7 * k), ws - timedelta(days=7 * k - 6)
        if ledger.first_date is None or s < ledger.first_date:
            break
        txs = [t for t in ledger.expenses_between(s, e) if flexible(t) and t.id not in unusual_ctx]
        prev_weeks.append(sum(t.outflow for t in txs))
        per_cat = defaultdict(float)
        for t in ledger.expenses_between(s, e):
            if flexible(t):
                per_cat[t.category] += t.outflow
        for c in set(per_cat) | set(prev_cat):
            prev_cat[c].append(per_cat.get(c, 0.0))
    typical = mean(prev_weeks) if prev_weeks else None

    # Month-to-date pace and projection.
    month = month_key(we)
    ms, me = month_start(month), month_end(month)
    mtd = ledger.spend(ms, we)
    remaining_days = (me - we).days
    upcoming_month = [r for r in (A.active_recurring if A else []) if we < r.next_date <= me]
    expected_fixed = sum(r.last_amount for r in upcoming_month)
    look_start = max(we - timedelta(days=89), ledger.first_date or we)
    flex_recent = [t for t in ledger.expenses_between(look_start, we) if flexible(t) and t.id not in unusual_ctx]
    daily = sum(t.outflow for t in flex_recent) / max(1, (we - look_start).days + 1)
    projected = round(mtd + expected_fixed + daily * remaining_days, 2)
    budget_total = ledger.budget_total(month)
    plan = budget_total or (A.avg_spend if A else None)
    plan_label = "budget" if budget_total else "your 6-month average"

    headline = f"You spent {m(total)} this week"
    if typical:
        diff = flex - typical
        headline += (f" — flexible spending was {m(abs(diff))} {'above' if diff > 0 else 'below'} a typical week."
                     if abs(diff) >= 10 else " — right in line with a typical week.")
    else:
        headline += "."

    r = Report(kind="weekly", title="Weekly money check-in", period_label=fmt_range(ws, we), start=ws, end=we,
               headline=headline, household=ledger.config["household"]["name"], currency=cur)

    tone_week = ""
    if typical:
        tone_week = "bad" if flex > typical * 1.15 else "good" if flex < typical * 0.9 else ""
    kpis = [
        {"label": "Spent this week", "value": m(total),
         "sub": f"{m(flex)} flexible · {m(total - flex)} bills & giving"},
        {"label": "Flexible vs. typical week", "value": m(flex),
         "sub": (f"typical week {m(typical)}" if typical else "building a baseline"), "tone": tone_week},
        {"label": f"{month_label(month).split()[0]} so far", "value": m(mtd),
         "sub": f"of {m(plan)} {plan_label}" if plan else f"through {fmt_date(we)}"},
    ]
    if plan:
        over = projected - plan
        kpis.append({"label": "Projected month-end", "value": m(projected),
                     "sub": f"{m(abs(over))} {'over' if over > 0 else 'under'} {plan_label}",
                     "tone": "bad" if over > plan * 0.02 else "good"})
    r.add("kpis", items=kpis)

    # Pace guidance — the single most actionable thing in a weekly report.
    if plan and remaining_days > 0:
        room = plan - mtd - expected_fixed
        weeks_left = remaining_days / 7
        per_week = room / weeks_left if weeks_left else room
        if room <= 0:
            r.add("callout", tone="bad", title="This month's plan is already used up",
                  body=f"With {m(expected_fixed)} in bills still to come, {month_label(month)} will finish about "
                       f"{m(projected - plan)} over {plan_label}. Keep the remaining {remaining_days} days to "
                       f"essentials only and push any non-urgent purchases to next month.")
        elif projected > plan * 1.02:
            r.add("callout", tone="warn", title=f"On pace to finish {m(projected - plan)} over {plan_label}",
                  body=f"To land on target, keep flexible spending to about {m(per_week)}/week for the rest of "
                       f"the month (you've been averaging {m(daily * 7)}/week). {m(expected_fixed)} in recurring "
                       f"bills is still due before {fmt_date(me)}.")
        else:
            r.add("callout", tone="good", title="On track for the month",
                  body=f"Projected {m(projected)} vs {m(plan)} {plan_label}. You can spend about {m(per_week)}/week "
                       f"on flexible items and still land on target.")

    # Alerts that need attention this week.
    alerts: list[Insight] = []
    for f in find_fees(ledger.expenses, ws, we):
        alerts.append(Insight(key=f"fee:{f.txn.id}", title=f"{f.label}: {m(f.txn.outflow)} ({fmt_date(f.txn.date)})",
                              detail=f"{f.txn.payee} · {f.txn.account}" if f.txn.account else f.txn.payee,
                              action=_fee_action(f.type),
                              one_time=f.txn.outflow if f.type in ("overdraft", "late") else 0.0,
                              severity="high" if f.type in ("overdraft", "late", "interest") else "medium",
                              kind="recover"))
    for a, b in find_duplicates(ledger.expenses, ws, we):
        alerts.append(Insight(key=f"dup:{b.id}", title=f"Possible double charge: {m(b.outflow)} at {b.merchant}",
                              detail=f"{fmt_date(a.date)} and {fmt_date(b.date)}",
                              action="Check the statement and dispute it if you were charged twice.",
                              one_time=b.outflow, severity="high", kind="recover"))
    floor = float(ledger.config.get("analysis", {}).get("large_transaction", 150.0))
    for t, why in find_unusual(ledger.expenses, ws, we, floor, recurring_ids):
        alerts.append(Insight(key=f"big:{t.id}", title=f"Large purchase: {m(t.outflow)} at {t.merchant}",
                              detail=f"{fmt_date(t.date)} · {why}.",
                              action="Planned? If one-offs like this keep showing up, give them their own "
                                     "monthly line so they don't derail the budget.", severity="low"))
    for rc in (A.recurring if A else []):
        hit = [t for t in week if t.id in rc.charge_ids]
        if not hit:
            continue
        if rc.is_new and rc.first_date >= ws:
            alerts.append(Insight(key=f"new:{rc.merchant}", title=f"New recurring charge: {rc.merchant} {m(rc.last_amount)}",
                                  detail="First time we've seen this charge.",
                                  action="Make sure it's intentional — trials often convert silently.",
                                  monthly_impact=rc.monthly_cost, severity="medium"))
        elif rc.price_change > 0 and rc.last_date >= ws:
            alerts.append(Insight(key=f"price:{rc.merchant}",
                                  title=f"{rc.merchant} price went up: {m(rc.previous_amount)} → {m(rc.last_amount)}",
                                  detail="Charged this week at the new price.",
                                  action="Still worth it? Consider a cheaper tier or rotating it out.",
                                  monthly_impact=rc.price_change, severity="low"))
    if alerts:
        r.add("insights", title="Needs your attention", items=alerts, compact=True)

    # Where it went.
    by_cat = ledger.spend_by_category(ws, we)
    flex_cat = ledger.spend_by_category(ws, we, [t for t in week if flexible(t)])
    rows = []
    for cat, amt in list(by_cat.items())[:12]:
        if amt <= 0:
            continue
        fx = flex_cat.get(cat, 0.0)
        avg = mean(prev_cat.get(cat, [])) if prev_cat.get(cat) else None
        tone = "bad" if avg is not None and fx > avg * 1.5 and fx - avg >= 25 else ""
        if fx < 0.5:
            sub = "bills / recurring"
        else:
            sub = f"typical week {m(avg)}" if avg else "new this week"
        rows.append({"label": cat, "value": amt, "display": m(amt), "sub": sub, "tone": tone})
    r.add("bars", title="Where it went this week", rows=rows,
          note="Red = flexible spending well above a typical week for that category. "
               "Bills and subscriptions are never marked red.")

    # Category pace for the month (only flexible categories with a budget).
    if budget_total:
        bill_cats = {rc.category for rc in (A.active_recurring if A else []) if rc.kind in ("bill", "subscription")}
        frac = (we - ms).days + 1
        expected = frac / days_in_month(month)
        pace_rows = []
        for cat, bud in sorted(ledger.budget_map.get(month, {}).items()):
            if ledger.class_of(cat) in (CLASS_SAVINGS, CLASS_GIVING, CLASS_DEBT) or bud <= 0 or cat in bill_cats:
                continue
            txs = [t for t in ledger.expenses_between(ms, we) if t.category == cat]
            spent = sum(t.outflow for t in txs)
            fixed = sum(t.outflow for t in txs if t.id in recurring_ids)
            if fixed and fixed >= 0.7 * spent:
                continue
            used = spent / bud
            if spent > bud:
                status = _cell("over", "bad")
            elif used > expected + 0.4:
                status = _cell("at risk", "bad")
            elif used > expected + 0.15:
                status = _cell("ahead of pace", "warn")
            else:
                status = _cell("on track", "good")
            pace_rows.append((used, [cat, m(spent), m(bud), m(bud - spent), f"{used * 100:.0f}%", status]))
        pace_rows.sort(key=lambda x: -x[0])
        if pace_rows:
            r.add("table", title=f"{month_label(month).split()[0]} budget pace ({expected * 100:.0f}% of the month gone)",
                  columns=["Category", "Spent", "Budget", "Left", "Used", "Status"],
                  align=["l", "r", "r", "r", "r", "l"], rows=[row for _, row in pace_rows[:10]])

    # Coming up.
    if A:
        soon = sorted([rc for rc in A.active_recurring if we < rc.next_date <= we + timedelta(days=7)],
                      key=lambda rc: rc.next_date)
        if soon:
            r.add("list", title=f"Coming up in the next 7 days ({m(sum(rc.last_amount for rc in soon))})",
                  items=[f"{fmt_date(rc.next_date)} — {rc.merchant} ~{m(rc.last_amount)}" for rc in soon])

        focus = [i for i in A.insights if i.kind == "save"][:2]
        if focus:
            r.add("list", title="Focus for this week",
                  items=[f"{i.title}. {i.action}" for i in focus], ordered=True)

    week_sorted = sorted(week, key=lambda t: (t.date, -t.outflow))
    r.add("transactions", title=f"Every expense this week ({len(week)})", rows=week_sorted,
          note=f"Income received this week: {m(income_week)}." if income_week else "")
    r.itemized = week_sorted
    r.summary = {
        "kind": "weekly", "week": fmt_range(ws, we), "spent": total, "flexible": flex, "typical_flexible_week": typical,
        "income_this_week": income_week, "month_to_date": mtd, "projected_month_end": projected,
        "plan": plan, "plan_label": plan_label,
        "by_category": by_cat, "alerts": [a.to_dict() for a in alerts],
        "context_insights": [i.to_dict() for i in (A.insights[:5] if A else [])],
    }
    return r


def _first_sentence(text: str) -> str:
    import re
    m = re.search(r"(?<!e\.g)(?<!i\.e)(?<!vs)\.\s", text)
    return text if not m else text[: m.start() + 1]


def _fee_action(kind: str) -> str:
    from finagent.detectors import FEE_ADVICE
    return FEE_ADVICE.get(kind, FEE_ADVICE["other"])


# --------------------------------------------------------------------------- monthly

def build_monthly(ledger: Ledger, month: str, A: Analysis) -> Report:
    cur = ledger.currency
    m = _money(cur)
    ms, me = month_start(month), month_end(month)
    spend, income = ledger.spend_month(month), ledger.income_month(month)
    net = income - spend
    rate = net / income if income else None
    prev = shift_month(month, -1)
    prev_spend = ledger.spend_month(prev) if ledger.first_date and month_start(prev) >= ledger.first_date - timedelta(days=4) else None
    prior3 = [x for x in A.months if x < month][-3:]
    avg3 = mean([ledger.spend_month(x) for x in prior3]) if prior3 else None
    budget_total = ledger.budget_total(month)

    name = month_label(month)
    if budget_total:
        vs = f"{m(abs(spend - budget_total))} {'over' if spend > budget_total else 'under'} budget"
    elif avg3:
        vs = f"{m(abs(spend - avg3))} {'above' if spend > avg3 else 'below'} your recent average"
    else:
        vs = ""
    headline = f"{name}: you spent {m(spend)}" + (f" ({vs})" if vs else "") + f" and brought in {m(income)}"
    headline += (f", keeping {m(net)} ({rate * 100:.0f}% of income)." if net >= 0 and rate is not None
                 else f" — {m(-net)} more went out than came in." if income else ".")

    r = Report(kind="monthly", title="Monthly budget report", period_label=name, start=ms, end=me,
               headline=headline, household=ledger.config["household"]["name"], currency=cur)

    s_prev, t_prev = _delta_text(m, spend, prev_spend, "last month")
    kpis = [
        {"label": "Income", "value": m(income),
         "sub": _delta_text(m, income, ledger.income_month(prev) if prev_spend is not None else None,
                            "last month", lower_is_good=False)[0]},
        {"label": "Spending", "value": m(spend), "sub": s_prev, "tone": t_prev},
        {"label": "Kept" if net >= 0 else "Overspent", "value": m(abs(net)),
         "sub": f"{rate * 100:.0f}% of income (target {A.savings_target * 100:.0f}%)" if rate is not None else "",
         "tone": "good" if rate is not None and rate >= A.savings_target else "bad" if net < 0 else ""},
    ]
    if budget_total:
        kpis.append({"label": "vs. budget", "value": m(abs(spend - budget_total)),
                     "sub": f"{'over' if spend > budget_total else 'under'} a {m(budget_total)} budget",
                     "tone": "bad" if spend > budget_total else "good"})
    elif avg3:
        kpis.append({"label": "vs. 3-month average", "value": m(abs(spend - avg3)),
                     "sub": f"{'above' if spend > avg3 else 'below'} {m(avg3)}", "tone": "bad" if spend > avg3 else "good"})
    r.add("kpis", items=kpis)

    if A.insights:
        r.add("insights", title="What to do next", items=A.insights[:6], total_monthly=A.potential_monthly,
              total_one_time=A.potential_one_time,
              note=f"Based on the {A.n} months ending {name}. Giving and saving are never suggested as cuts.")

    # Budget vs actual.
    cats = ledger.spend_by_category(ms, me)
    budgeted = ledger.budget_map.get(month, {})
    all_cats = set(cats) | {c for c, b in budgeted.items() if b and ledger.class_of(c) != CLASS_SAVINGS}
    by_group: dict[str, list] = defaultdict(list)
    for c in all_cats:
        by_group[ledger.group_of(c)].append(c)
    rows = []
    prior_matrix = ledger.category_by_month(prior3) if prior3 else {}
    group_order = sorted(by_group, key=lambda g: -sum(cats.get(c, 0.0) for c in by_group[g]))
    for g in group_order:
        for c in sorted(by_group[g], key=lambda c: -cats.get(c, 0.0)):
            spent = cats.get(c, 0.0)
            bud = budgeted.get(c)
            avg = mean([prior_matrix.get(c, {}).get(x, 0.0) for x in prior3]) if prior3 else None
            if bud:
                left = bud - spent
                diff_cell = _cell(m(left) if left >= 0 else f"−{m(-left)}", "good" if left >= 0 else "bad")
            else:
                diff_cell = _cell("—")
            trend = ""
            if avg and spent and abs(spent - avg) >= max(20, 0.15 * avg):
                trend = _cell(f"{'▲' if spent > avg else '▼'} {abs(spent - avg) / avg * 100:.0f}%",
                              "bad" if spent > avg else "good")
            rows.append([c, g, m(spent), m(bud) if bud else "—", diff_cell, m(avg) if avg is not None else "—", trend])
    r.add("table", title="Budget vs. actual" if budgeted else "Spending by category",
          columns=["Category", "Group", "Spent", "Budget", "Left / over", "3-mo avg", "vs avg"],
          align=["l", "l", "r", "r", "r", "r", "l"], rows=rows)

    r.sections.append(columns_section(A, f"Last {A.n} months"))
    r.sections.append(class_bars(A, m, f"Spending mix ({A.n}-month average)"))

    top = ledger.merchant_totals(ms, me)[:10]
    r.add("table", title="Top merchants", columns=["Merchant", "Charges", "Spent", "Share"],
          align=["l", "r", "r", "r"],
          rows=[[mer, str(cnt), m(v), f"{v / spend * 100:.0f}%" if spend else "—"] for mer, v, cnt in top])

    r.sections.append(recurring_table(A, m))

    flagged = [(t, "fee") for t in ledger.expenses_between(ms, me) if "fee" in t.flags]
    flagged += [(t, "possible duplicate") for t in ledger.expenses_between(ms, me) if "possible duplicate" in t.flags]
    flagged += [(t, why) for t, why in A.unusual if ms <= t.date <= me]
    if flagged:
        r.add("transactions", title="Flagged this month", rows=[t for t, _ in flagged],
              reasons={t.id: why for t, why in flagged})
    if A.wins:
        r.add("wins", title="Wins", items=A.wins)

    month_tx = ledger.expenses_between(ms, me)
    r.sections.append(itemization_section(ledger, month_tx, f"Itemized expenses — {name}",
                                          f"{len(month_tx)} transactions, transfers excluded. "
                                          f"Refunds show as negative."))
    r.itemized = sorted(month_tx, key=lambda t: t.date)
    summary = A.to_summary()
    summary.update({"kind": "monthly", "month": name, "spent": spend, "income": income, "net": net,
                    "budget_total": budget_total, "previous_month_spend": prev_spend, "three_month_avg": avg3,
                    "category_spend": cats})
    r.summary = summary
    return r


# --------------------------------------------------------------------------- review

def build_review(ledger: Ledger, A: Analysis) -> Report:
    cur = ledger.currency
    m = _money(cur)
    label = f"{month_label(A.months[0])} – {month_label(A.months[-1])}"
    r = Report(kind="review", title=f"{A.n}-month budget review", period_label=label, start=A.start, end=A.end,
               headline=A.headline(), household=ledger.config["household"]["name"], currency=cur)

    fixed = A.fixed_monthly()
    rate = A.savings_rate
    kpis = [
        {"label": "Avg monthly income", "value": m(A.avg_income), "sub": f"{A.n} months"},
        {"label": "Avg monthly spending", "value": m(A.avg_spend),
         "sub": f"{A.avg_spend / A.avg_income * 100:.0f}% of income" if A.avg_income else ""},
        {"label": "Kept per month", "value": m(A.avg_income - A.avg_spend),
         "sub": f"{rate * 100:.0f}% savings rate (target {A.savings_target * 100:.0f}%)" if rate is not None else "",
         "tone": "good" if rate is not None and rate >= A.savings_target else "bad"},
        {"label": "Fixed commitments", "value": m(fixed),
         "sub": f"{fixed / A.avg_income * 100:.0f}% of income is spoken for" if A.avg_income else "per month"},
        {"label": "Opportunity", "value": f"{m(A.potential_monthly)}/mo",
         "sub": f"{m(A.potential_monthly * 12)}/yr" + (f" + {m(A.potential_one_time)} to recover"
                                                       if A.potential_one_time else ""), "tone": "accent"},
    ]
    r.add("kpis", items=kpis)

    plan = [i for i in A.insights if i.kind in ("save", "recover")][:5]
    if plan:
        r.add("list", title="Your 30-day action plan", ordered=True,
              items=[f"**{i.title}.** {_first_sentence(i.action)}"
                     + (f" _(≈{money_auto(round(i.monthly_impact), cur)}/mo)_" if i.monthly_impact else
                        f" _(≈{m(i.one_time)} back)_" if i.one_time else "")
                     for i in plan],
              note="Details and the reasoning behind each step are in the next section.")

    r.add("insights", title="Everything we found", items=A.insights, total_monthly=A.potential_monthly,
          total_one_time=A.potential_one_time,
          note="Ranked by dollar impact. Giving and saving are never suggested as cuts.")

    r.sections.append(columns_section(A, "Income vs. spending by month"))
    rows = []
    for x in A.monthly:
        bud = x["budget"]
        rows.append([month_label(x["month"]), m(x["income"]), m(x["spend"]),
                     _cell(m(x["net"]) if x["net"] >= 0 else f"−{m(-x['net'])}", "good" if x["net"] >= 0 else "bad"),
                     f"{x['rate'] * 100:.0f}%" if x["rate"] is not None else "—",
                     m(bud) if bud else "—",
                     _cell((m(x["spend"] - bud) + " over") if bud and x["spend"] > bud else
                           (m(bud - x["spend"]) + " under") if bud else "—",
                           "bad" if bud and x["spend"] > bud else "good" if bud else "")])
    r.add("table", title="Month by month", columns=["Month", "Income", "Spending", "Net", "Saved", "Budget", "vs budget"],
          align=["l", "r", "r", "r", "r", "r", "l"], rows=rows)

    # Where the money goes + trend.
    averages = sorted(((c, A.category_avg(c)) for c in A.cat_matrix), key=lambda x: -x[1])
    total_avg = sum(v for _, v in averages if v > 0) or 1
    bar_rows, trend_rows = [], []
    for c, avg in averages:
        if avg <= 0:
            continue
        # Trends ignore one-off purchases (those are covered by the irregular-expenses suggestion).
        recent = A.category_avg(c, A.recent, core=True)
        prior = A.category_avg(c, A.prior, core=True) if A.prior else None
        trend, tone = "", ""
        if prior and abs(recent - prior) >= max(25, 0.15 * prior):
            trend = f"{'▲' if recent > prior else '▼'} {abs(recent - prior) / prior * 100:.0f}% lately"
            tone = "bad" if recent > prior and not ledger.is_protected(c) else "good" if recent < prior else ""
        bar_rows.append({"label": c, "value": avg, "display": m(avg),
                         "sub": f"{avg / total_avg * 100:.0f}%" + (f" · {trend}" if trend else ""), "tone": tone})
        budgets = [ledger.budget(x, c) for x in A.months]
        bud_vals = [b for b in budgets if b]
        months_over = sum(1 for x, b in zip(A.months, budgets) if b and A.cat_matrix[c].get(x, 0) > b * 1.05 + 5)
        trend_rows.append([c, ledger.group_of(c), m(avg), m(prior) if prior is not None else "—", m(recent),
                           _cell(trend.replace(" lately", ""), tone) if trend else "",
                           m(mean(bud_vals)) if bud_vals else "—",
                           _cell(f"{months_over}/{len(bud_vals)}", "bad" if bud_vals and months_over > len(bud_vals) / 2
                                 else "") if bud_vals else "—"])
    r.add("bars", title="Where the money goes (average per month)", rows=bar_rows[:18],
          note="Percent of total spending. Arrows compare the last few months to the ones before, "
               "leaving out one-off purchases.")
    r.add("table", title="Category trends & budget adherence",
          columns=["Category", "Group", "Avg/mo", "Before", "Lately", "Change", "Budget", "Months over"],
          align=["l", "l", "r", "r", "r", "l", "r", "r"], rows=trend_rows)
    r.sections.append(class_bars(A, m))
    r.sections.append(recurring_table(A, m))
    if A.wins:
        r.add("wins", title="What's working", items=A.wins)

    window = A.window_expenses()
    cat_totals = ledger.spend_by_category(A.start, A.end)
    grand = sum(cat_totals.values()) or 1
    r.add("table", title="Itemized totals by category",
          columns=["Category", "Group", "Transactions", "Total", "Per month", "Share"],
          align=["l", "l", "r", "r", "r", "r"],
          rows=[[c, ledger.group_of(c), str(sum(1 for t in window if (t.category or "Uncategorized") == c)), m(v),
                 m(v / A.n), f"{v / grand * 100:.1f}%"] for c, v in cat_totals.items()],
          note=f"{len(window)} expense transactions · {m(grand)} total · transfers excluded.")
    r.sections.append(itemization_section(ledger, window, "Every expense, itemized",
                                          "Grouped by category. Refunds show as negative amounts."))
    r.itemized = sorted(window, key=lambda t: t.date)
    r.summary = A.to_summary() | {"kind": "review"}
    return r
