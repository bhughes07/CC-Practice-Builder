"""Turn numbers into ranked, dollar-quantified, specific actions.

Every insight answers three questions: what happened (detail), what to do
(action), and what it's worth (monthly_impact / one_time). Protected
categories (giving, saving) are never suggested as cuts.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date

from finagent.detectors import FEE_ADVICE, Fee, find_duplicates, find_fees, find_unusual
from finagent.ledger import Ledger
from finagent.models import (
    CLASS_DEBT, CLASS_DISCRETIONARY, CLASS_ESSENTIAL, CLASS_FEES, CLASS_GIVING, CLASS_INCOME, CLASS_TRANSFER,
    Transaction,
)
from finagent.recurring import Recurring, detect_recurring
from finagent.util import fmt_date, mean, money_auto, month_end, month_label, month_start

DELIVERY = {"DoorDash", "Uber Eats", "Grubhub", "Postmates", "Caviar"}
COFFEE = {"Starbucks", "Dutch Bros", "Dunkin'"}
SEVERITY_WEIGHT = {"high": 300, "medium": 100, "low": 0}


@dataclass
class Insight:
    key: str
    title: str
    detail: str
    action: str
    monthly_impact: float = 0.0
    one_time: float = 0.0
    severity: str = "medium"  # high | medium | low
    area: str = ""
    kind: str = "save"  # save | recover | protect | opportunity
    evidence: list[str] = field(default_factory=list)
    always_show: bool = False  # bypass the small-impact filter (price hikes, new subscriptions)

    @property
    def annual_impact(self) -> float:
        return round(self.monthly_impact * 12, 2)

    @property
    def score(self) -> float:
        return self.monthly_impact * 12 + self.one_time + SEVERITY_WEIGHT.get(self.severity, 0)

    def to_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "detail": self.detail, "action": self.action,
                "monthly_impact": round(self.monthly_impact, 2), "annual_impact": self.annual_impact,
                "one_time": round(self.one_time, 2), "severity": self.severity, "area": self.area,
                "kind": self.kind, "evidence": self.evidence}


@dataclass
class Win:
    title: str
    detail: str


def _severity(monthly: float, one_time: float = 0.0) -> str:
    v = monthly * 12 + one_time
    return "high" if v >= 1500 else "medium" if v >= 400 else "low"


def _round_to(x: float, step: float = 25.0) -> float:
    return max(step, round(x / step) * step)


class Analysis:
    """All the analysis for a window of complete months, computed once and shared by reports."""

    def __init__(self, ledger: Ledger, months: list[str]):
        if not months:
            raise ValueError("No complete months of data to analyze yet.")
        self.ledger = ledger
        self.months = months
        self.n = len(months)
        self.today: date = ledger.today
        self.start, self.end = month_start(months[0]), month_end(months[-1])
        cfg = ledger.config.get("analysis", {})
        self.min_impact = float(cfg.get("min_monthly_impact", 5.0))
        self.savings_target = float(cfg.get("savings_rate_target", 0.15))
        m = lambda v: money_auto(v, ledger.currency)  # noqa: E731
        self.m = m

        self.recurring: list[Recurring] = detect_recurring(ledger.expenses, self.today)
        self.active_recurring = [r for r in self.recurring if r.active]
        self.recurring_ids = {i for r in self.recurring for i in r.charge_ids}
        for t in ledger.expenses:
            if t.id in self.recurring_ids:
                t.flags.add("recurring")
        self.fees: list[Fee] = find_fees(ledger.expenses, self.start, self.end)
        self.duplicates = find_duplicates(ledger.expenses, self.start, self.end)
        self.unusual = find_unusual(ledger.expenses, self.start, self.end,
                                    float(cfg.get("large_transaction", 150.0)), self.recurring_ids)
        self.unusual_ids = {t.id for t, _ in self.unusual}

        self.monthly = []
        for mo in months:
            inc, sp = ledger.income_month(mo), ledger.spend_month(mo)
            self.monthly.append({
                "month": mo, "label": month_label(mo, short=True), "income": inc, "spend": sp,
                "net": round(inc - sp, 2), "rate": (inc - sp) / inc if inc else None,
                "budget": ledger.budget_total(mo),
            })
        self.cat_matrix = ledger.category_by_month(months)
        self.cat_matrix_core = ledger.category_by_month(months, exclude_ids=self.unusual_ids)
        self.recent_n = min(3, max(1, self.n // 2))
        self.recent, self.prior = months[-self.recent_n:], months[:-self.recent_n]

        self.insights: list[Insight] = []
        self.wins: list[Win] = []
        self._covered: dict[str, Insight] = {}
        self._build()

    # ------------------------------------------------------------------ summary numbers
    @property
    def avg_income(self) -> float:
        return mean([x["income"] for x in self.monthly])

    @property
    def avg_spend(self) -> float:
        return mean([x["spend"] for x in self.monthly])

    @property
    def savings_rate(self) -> float | None:
        inc = sum(x["income"] for x in self.monthly)
        return (inc - sum(x["spend"] for x in self.monthly)) / inc if inc else None

    @property
    def potential_monthly(self) -> float:
        return round(sum(i.monthly_impact for i in self.insights), 2)

    @property
    def potential_one_time(self) -> float:
        return round(sum(i.one_time for i in self.insights), 2)

    def category_avg(self, cat: str, months: list[str] | None = None, core: bool = False) -> float:
        row = (self.cat_matrix_core if core else self.cat_matrix).get(cat, {})
        ms = months or self.months
        return mean([row.get(mo, 0.0) for mo in ms])

    def fixed_monthly(self) -> float:
        """Recurring bills + subscriptions + giving, normalized to a month."""
        return round(sum(r.monthly_cost for r in self.active_recurring), 2)

    def class_split(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for cat, row in self.cat_matrix.items():
            cls = self.ledger.class_of(cat)
            out[cls] = out.get(cls, 0.0) + mean(list(row.values()))
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def window_expenses(self) -> list[Transaction]:
        return self.ledger.expenses_between(self.start, self.end)

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        self._category_insights()
        self._behavior_insights()
        self._subscription_insights()
        self._fee_insight()
        self._duplicate_insights()
        self._sinking_fund_insight()
        self._underspent_insight()
        self._wins()
        self.insights = [i for i in self.insights
                         if i.monthly_impact >= self.min_impact or i.one_time > 0 or i.kind in ("protect", "opportunity")
                         or i.severity == "high" or i.always_show]
        self.insights.sort(key=lambda i: -i.score)

    def _txn_rate(self, cat: str) -> float:
        n = sum(1 for t in self.ledger.expenses_between(self.start, self.end) if t.category == cat and t.outflow > 0)
        return n / self.n

    def _merchant_drivers(self, cat: str, k: int = 3) -> list[str]:
        if not self.prior:
            return []
        rec: dict[str, float] = {}
        pri: dict[str, float] = {}
        for t in self.ledger.expenses_between(self.start, self.end):
            if t.category != cat or t.id in self.unusual_ids:
                continue
            bucket = rec if t.month in self.recent else pri
            bucket[t.merchant] = bucket.get(t.merchant, 0.0) + t.outflow
        deltas = []
        for mer in set(rec) | set(pri):
            r, p = rec.get(mer, 0.0) / len(self.recent), pri.get(mer, 0.0) / len(self.prior)
            if r - p >= 15:
                deltas.append((r - p, mer, r, p))
        deltas.sort(reverse=True)
        return [f"{mer}: {self.m(r)}/mo now vs {self.m(p)}/mo before (+{self.m(d)})" for d, mer, r, p in deltas[:k]]

    def _category_action(self, cat: str, target: float, cls: str) -> str:
        c = cat.lower()
        t = self.m(target)
        if re.search(r"dining|restaurant|eating out|takeout|fast food", c):
            return (f"Set a {t}/mo dining target and plan eat-out nights in advance (e.g. one family dinner out "
                    f"+ one lunch a week). Check the weekly report to stay on pace.")
        if re.search(r"grocer|food at home|supermarket", c):
            return (f"Aim for {t}/mo: plan the week's meals before shopping, shop with a list, and use one "
                    f"big-box run per month for staples instead of several.")
        if re.search(r"coffee", c):
            return f"Aim for {t}/mo: brew at home on weekdays and keep the coffee shop for a weekend treat."
        if re.search(r"shop|amazon|clothing|household|home goods", c):
            return (f"Cap it at {t}/mo. Use a 48-hour rule for anything over $30 (leave it in the cart) and "
                    f"batch online orders to one day a week.")
        if re.search(r"entertain|fun|recreation|hobb", c):
            return f"Set a {t}/mo fun budget and look for free/low-cost options (parks, library, church events)."
        if re.search(r"gas|fuel|transport", c):
            return f"Target {t}/mo: combine errands into one trip, and use a gas-rewards app or warehouse club fuel."
        if re.search(r"electric|utilit|water|power", c):
            return "Ask your provider about a fixed-rate or budget-billing plan; set the thermostat 2° closer to outside temp."
        return f"Set a monthly cap of {t} for {cat} and watch it in the weekly report."

    def _category_insights(self) -> None:
        L = self.ledger
        half = math.ceil(self.n / 2)
        for cat, row in self.cat_matrix_core.items():
            cls = L.class_of(cat)
            if L.is_protected(cat) or cls in (CLASS_DEBT, CLASS_TRANSFER, CLASS_INCOME, CLASS_FEES, CLASS_GIVING):
                continue
            total = sum(row.values())
            if total <= 0:
                continue
            fixed = sum(t.outflow for t in self.ledger.expenses_between(self.start, self.end)
                        if t.category == cat and t.id in self.recurring_ids)
            fixed_share = fixed / total
            recent_avg = mean([row[mo] for mo in self.recent])
            prior_avg = mean([row[mo] for mo in self.prior]) if self.prior else 0.0
            growth = recent_avg - prior_avg if self.prior else 0.0
            budgets = [(mo, L.budget(mo, cat)) for mo in self.months]
            budgeted = [(mo, b) for mo, b in budgets if b]
            avg_budget = mean([b for _, b in budgeted]) if budgeted else 0.0
            # Growth must be real: enough purchases to be a habit (lumpy categories are handled by the
            # sinking-fund insight), sustained across recent months, and above the plan if there is one.
            per_month = self._txn_rate(cat)
            sustained = sum(1 for mo in self.recent if row[mo] > prior_avg) >= max(1, len(self.recent) - 1)
            growth_flag = (bool(self.prior) and prior_avg > 0 and fixed_share < 0.7 and per_month >= 2
                           and sustained and growth >= max(50.0, 0.15 * prior_avg)
                           and (not avg_budget or recent_avg > avg_budget))

            over = [(mo, row[mo] - b) for mo, b in budgeted if row[mo] > b * 1.05 + 5]
            overspend_avg = mean([max(0.0, row[mo] - b) for mo, b in budgeted]) if budgeted else 0.0
            over_flag = len(budgeted) >= half and len(over) >= half and overspend_avg >= 25 and fixed_share < 0.7

            if not (growth_flag or over_flag):
                continue
            use_budget = over_flag and (not growth_flag or overspend_avg >= growth)
            impact = overspend_avg if use_budget else growth
            target = avg_budget if use_budget else _round_to(prior_avg)
            evidence = self._merchant_drivers(cat)
            if growth_flag:
                title = (f"{cat} is up {growth / prior_avg * 100:.0f}%: {self.m(recent_avg)}/mo lately vs "
                         f"{self.m(prior_avg)}/mo before")
                detail = (f"Over the last {self.recent_n} months {cat} averaged {self.m(recent_avg)}/mo, "
                          f"{self.m(growth)} more than the {len(self.prior)} months before.")
            else:
                title = f"{cat} ran over budget in {len(over)} of {len(budgeted)} months"
                detail = (f"{cat} averaged {self.m(self.category_avg(cat, core=True))}/mo against a "
                          f"{self.m(avg_budget)} budget — about {self.m(overspend_avg)}/mo over.")
            if over_flag and growth_flag:
                detail += f" It also went over its {self.m(avg_budget)} budget in {len(over)} of {len(budgeted)} months."
            action = self._category_action(cat, target, cls)
            if not use_budget and avg_budget and avg_budget < target:
                action += (f" (Your budget says {self.m(avg_budget)} — get back to {self.m(target)} first, "
                           f"then work down.)")
            if over_flag and not growth_flag and len(over) >= len(budgeted) - 1 and growth < 25:
                real = _round_to(self.category_avg(cat, core=True))
                action = (f"Reset this budget to a realistic {self.m(real)} (and trim another category to match), "
                          f"or commit to {self.m(avg_budget)} with specific changes — it has been unrealistic "
                          f"every month. " + action)
            ins = Insight(key=f"category:{cat}", title=title, detail=detail, action=action,
                          monthly_impact=round(impact, 2), severity=_severity(impact), area=cat, evidence=evidence)
            self.insights.append(ins)
            self._covered[cat] = ins

    def _behavior_insights(self) -> None:
        L = self.ledger
        rec = [t for t in L.expenses_between(month_start(self.recent[0]), self.end) if t.id not in self.unusual_ids]

        def monthly(merchants: set[str]) -> tuple[float, float, str]:
            hits = [t for t in rec if t.merchant in merchants]
            cats: dict[str, float] = {}
            for t in hits:
                cats[t.category] = cats.get(t.category, 0.0) + t.outflow
            main = max(cats, key=cats.get) if cats else ""
            return sum(t.outflow for t in hits) / len(self.recent), len(hits) / len(self.recent), main

        # Food delivery
        spend, orders, cat = monthly(DELIVERY)
        if spend >= 75:
            line = (f"Delivery: {self.m(spend)}/mo across ~{orders:.0f} orders (avg {self.m(spend / max(orders, 1))}). "
                    f"Fees and menu markups typically add 25–40% vs. pickup.")
            if cat in self._covered:
                self._covered[cat].evidence.insert(0, line)
                self._covered[cat].action += " Swap delivery for pickup — same food, ~30% cheaper."
            else:
                self.insights.append(Insight(
                    key="delivery", title=f"Food delivery is {self.m(spend)}/mo", detail=line,
                    action="Switch to pickup for half your orders and keep a few 15-minute 'emergency dinners' "
                           "in the pantry for busy nights.",
                    monthly_impact=round(spend * 0.3, 2), severity=_severity(spend * 0.3), area=cat or "Dining Out"))

        # Coffee shops
        spend, visits, cat = monthly(COFFEE)
        if spend >= 40:
            line = f"Coffee shops: {self.m(spend)}/mo, ~{visits:.0f} visits/mo."
            if cat in self._covered:
                self._covered[cat].evidence.insert(0, line)
            else:
                self.insights.append(Insight(
                    key="coffee", title=f"Coffee runs: ~{visits:.0f} visits, {self.m(spend)}/mo", detail=line,
                    action="Brew at home on weekdays (a good bag of beans is ~$12) and keep the coffee shop as a "
                           "weekend treat.",
                    monthly_impact=round(spend * 0.5, 2), severity=_severity(spend * 0.5), area=cat or "Coffee"))

        # Amazon
        amazon = [t for t in L.expenses_between(self.start, self.end) if t.merchant == "Amazon"
                  and t.id not in self.unusual_ids]
        if amazon:
            spend = sum(t.outflow for t in amazon) / self.n
            orders = sum(1 for t in amazon if t.outflow > 0) / self.n
            cats: dict[str, float] = {}
            for t in amazon:
                cats[t.category] = cats.get(t.category, 0.0) + t.outflow
            cat = max(cats, key=cats.get)
            if spend >= 150:
                line = f"Amazon: {self.m(spend)}/mo across ~{orders:.0f} orders (avg {self.m(spend / max(orders, 1))})."
                if cat in self._covered:
                    ev = [e for e in self._covered[cat].evidence if not e.startswith("Amazon:")]
                    self._covered[cat].evidence = [line] + ev
                else:
                    self.insights.append(Insight(
                        key="amazon", title=f"Amazon averages {self.m(spend)}/mo (~{orders:.0f} orders)", detail=line,
                        action="Try a 48-hour cart rule and one 'order day' per week; unsubscribe from deal emails. "
                               "Small, frequent orders are where the leak is.",
                        monthly_impact=round(spend * 0.2, 2), severity=_severity(spend * 0.2), area=cat))

    def _subscription_insights(self) -> None:
        subs = [r for r in self.active_recurring if r.kind == "subscription"]
        flagged_new: set[str] = set()
        for r in self.active_recurring:
            if r.price_change > 0 and r.kind in ("subscription", "other"):
                per_month = r.monthly_cost / r.last_amount if r.last_amount else 1
                delta = r.price_change * per_month
                since = f" in {r.price_changed_on.strftime('%B')}" if r.price_changed_on else ""
                self.insights.append(Insight(
                    key=f"price:{r.merchant}",
                    title=f"{r.merchant} raised its price: {self.m(r.previous_amount)} → {self.m(r.last_amount)}",
                    detail=f"{r.merchant} went up {self.m(r.price_change)}{since} "
                           f"({self.m(delta * 12)}/yr more). Price increases are when people stop noticing.",
                    action=f"Decide if {r.merchant} is still worth {self.m(r.last_amount)}/{_per(r.cadence)}: "
                           f"switch to a cheaper tier, or cancel and rotate it with another service.",
                    monthly_impact=round(delta, 2), severity="low", area="Subscriptions", always_show=True))
            if r.is_new and r.kind in ("subscription", "other"):
                flagged_new.add(r.merchant)
                self.insights.append(Insight(
                    key=f"new:{r.merchant}",
                    title=f"New recurring charge: {r.merchant} {self.m(r.last_amount)}/{_per(r.cadence)}",
                    detail=f"First charged {fmt_date(r.first_date, True)}; {r.count} charges so far. "
                           f"Next expected around {fmt_date(r.next_date)}.",
                    action="Confirm this was intentional — free trials often convert silently. If nobody's using it, "
                           f"cancel before {fmt_date(r.next_date)}.",
                    monthly_impact=r.monthly_cost, severity="medium" if r.monthly_cost >= 10 else "low",
                    area="Subscriptions", always_show=True))
        streaming = [r for r in subs if r.is_streaming and r.merchant not in flagged_new]
        if len(streaming) >= 3:
            streaming.sort(key=lambda r: -r.monthly_cost)
            total = sum(r.monthly_cost for r in streaming)
            keep = streaming[:2]
            savings = total - sum(r.monthly_cost for r in keep)
            self.insights.append(Insight(
                key="streaming-stack",
                title=f"{len(streaming)} streaming services cost {self.m(total)}/mo ({self.m(total * 12)}/yr)",
                detail="Paying for everything at once: " + ", ".join(
                    f"{r.merchant} {self.m(r.monthly_cost)}" for r in streaming) + ".",
                action="Rotate instead of stacking: keep two at a time, cancel the rest, and switch when you've "
                       "finished a show. Most services let you resubscribe in one click.",
                monthly_impact=round(savings, 2), severity=_severity(savings), area="Subscriptions"))
        total_subs = sum(r.monthly_cost for r in subs)
        if subs and total_subs >= 40:
            self.insights.append(Insight(
                key="subscription-audit",
                title=f"Subscription audit: {len(subs)} active, {self.m(total_subs)}/mo ({self.m(total_subs * 12)}/yr)",
                detail="Active: " + ", ".join(f"{r.merchant} {self.m(r.monthly_cost)}" for r in
                                             sorted(subs, key=lambda r: -r.monthly_cost)) + ".",
                action="Spend 10 minutes together going down this list. Cancel anything nobody used in the last "
                       "30 days, and check for annual-plan discounts on the ones you keep (often 15–20% off).",
                monthly_impact=0.0, severity="low", area="Subscriptions", kind="opportunity"))

    def _fee_insight(self) -> None:
        if not self.fees:
            return
        total = sum(f.txn.outflow for f in self.fees)
        by_type: dict[str, list[Fee]] = {}
        for f in self.fees:
            by_type.setdefault(f.type, []).append(f)
        parts = [f"{len(v)}× {v[0].label} ({self.m(sum(x.txn.outflow for x in v))})" for v in by_type.values()]
        serious = any(k in by_type for k in ("overdraft", "late", "interest"))
        self.insights.append(Insight(
            key="fees",
            title=f"{self.m(total)} in avoidable fees over {self.n} months",
            detail="Fees: " + "; ".join(parts) + ".",
            action=" ".join(FEE_ADVICE[k] for k in by_type),
            monthly_impact=round(total / self.n, 2),
            severity="high" if serious else "medium", area="Fees",
            evidence=[f"{fmt_date(f.txn.date)} — {f.txn.payee} {self.m(f.txn.outflow)}" for f in self.fees[:8]]))

    def _duplicate_insights(self) -> None:
        for a, b in self.duplicates[:6]:
            self.insights.append(Insight(
                key=f"dup:{a.id}",
                title=f"Possible double charge: {self.m(b.outflow)} at {b.merchant}",
                detail=f"Charged {self.m(a.outflow)} on {fmt_date(a.date)} and again on {fmt_date(b.date)} "
                       f"({a.account or 'same account'}).",
                action="Check the receipt/statement. If it's a duplicate, ask the merchant to reverse it or dispute "
                       "it with your card issuer (most allow 60 days).",
                one_time=round(b.outflow, 2), severity="high", area=b.category, kind="recover"))

    def _sinking_fund_insight(self) -> None:
        total = sum(t.outflow for t, _ in self.unusual)
        if total < 300:
            return
        per_month = total / self.n
        fund = math.ceil(per_month / 25) * 25
        top = self.unusual[:4]
        self.insights.append(Insight(
            key="sinking-fund",
            title=f"One-off expenses averaged {self.m(per_month)}/mo — plan for them",
            detail="Irregular costs: " + "; ".join(
                f"{t.merchant} {self.m(t.outflow)} ({fmt_date(t.date)})" for t, _ in top) + ".",
            action=f"Create an 'Irregular expenses' category funded at {self.m(fund)}/mo (car repairs, medical, "
                   f"gifts, annual renewals). The surprises stop being emergencies.",
            monthly_impact=0.0, severity="medium", area="Planning", kind="protect"))

    def _underspent_insight(self) -> None:
        L = self.ledger
        if not L.has_budgets():
            return
        freed, lines = 0.0, []
        for cat, row in self.cat_matrix.items():
            if L.is_protected(cat):
                continue
            budgeted = [(mo, L.budget(mo, cat)) for mo in self.months if L.budget(mo, cat)]
            if len(budgeted) < math.ceil(self.n / 2):
                continue
            under = [b - row.get(mo, 0.0) for mo, b in budgeted]
            if sum(1 for u in under if u >= 25) >= 0.75 * len(budgeted):
                avg_under = mean(under)
                if avg_under >= 25:
                    freed += avg_under
                    lines.append(f"{cat}: spending {self.m(self.category_avg(cat))} vs {self.m(mean([b for _, b in budgeted]))} "
                                 f"budgeted (~{self.m(avg_under)}/mo unused)")
        if freed >= 25:
            self.insights.append(Insight(
                key="reallocate",
                title=f"{self.m(freed)}/mo of budget is consistently unused",
                detail="; ".join(lines) + ".",
                action="Lower these lines to what you really spend and give the difference a job — emergency fund, "
                       "extra debt payment, or the overspent categories above.",
                monthly_impact=0.0, severity="low", area="Budget", kind="opportunity", evidence=lines))

    def _wins(self) -> None:
        L = self.ledger
        if self.prior:
            for cat, row in self.cat_matrix_core.items():
                cls = L.class_of(cat)
                if cls not in (CLASS_DISCRETIONARY, CLASS_ESSENTIAL) or L.is_protected(cat):
                    continue
                r, p = mean([row[mo] for mo in self.recent]), mean([row[mo] for mo in self.prior])
                if p >= 50 and p - r >= max(40.0, 0.15 * p) and self._txn_rate(cat) >= 2:
                    self.wins.append(Win(f"{cat} is down {(p - r) / p * 100:.0f}%",
                                         f"{self.m(r)}/mo lately vs {self.m(p)}/mo before — {self.m(p - r)}/mo freed up."))
        for r in self.recurring:
            if not r.active and r.kind == "subscription" and r.last_date >= self.start:
                self.wins.append(Win(f"{r.merchant} looks cancelled",
                                     f"Last charged {fmt_date(r.last_date, True)} — saving {self.m(r.monthly_cost)}/mo "
                                     f"({self.m(r.annual_cost)}/yr)."))
        giving = {cat for cat in self.cat_matrix if L.class_of(cat) == CLASS_GIVING}
        if giving and self.avg_income:
            per_month = [sum(self.cat_matrix[c][mo] for c in giving) for mo in self.months]
            if all(v > 0 for v in per_month):
                avg = mean(per_month)
                self.wins.append(Win("Generous and consistent",
                                     f"You gave every month: {self.m(avg)}/mo on average "
                                     f"({avg / self.avg_income * 100:.0f}% of income)."))
        under = [x for x in self.monthly if x["budget"] and x["spend"] <= x["budget"]]
        if under:
            self.wins.append(Win(f"Stayed within the total budget in {len(under)} of {self.n} months",
                                 ", ".join(x["label"] for x in under) + "."))
        if self.prior:
            def rate(ms):
                inc = sum(x["income"] for x in self.monthly if x["month"] in ms)
                sp = sum(x["spend"] for x in self.monthly if x["month"] in ms)
                return (inc - sp) / inc if inc else None
            r, p = rate(self.recent), rate(self.prior)
            if r is not None and p is not None and r - p >= 0.03:
                self.wins.append(Win("Savings rate is improving",
                                     f"{r * 100:.0f}% of income kept lately vs {p * 100:.0f}% before."))

    # ------------------------------------------------------------------ headline
    def headline(self) -> str:
        rate = self.savings_rate
        if rate is None:
            return f"Average spending {self.m(self.avg_spend)}/mo over {self.n} months."
        kept = self.avg_income - self.avg_spend
        if kept < 0:
            s = (f"Over the last {self.n} months you spent {self.m(-kept)}/mo more than you brought in "
                 f"({self.m(self.avg_spend)} out vs {self.m(self.avg_income)} in).")
        else:
            s = (f"Over the last {self.n} months you kept {self.m(kept)}/mo — {rate * 100:.0f}% of income "
                 f"(target {self.savings_target * 100:.0f}%).")
        if self.potential_monthly > 0:
            new_rate = (kept + self.potential_monthly) / self.avg_income if self.avg_income else 0
            s += (f" Hitting the targets below would free up about {self.m(self.potential_monthly)}/mo "
                  f"({self.m(self.potential_monthly * 12)}/yr)")
            s += f", lifting that to ~{new_rate * 100:.0f}%." if self.avg_income else "."
        return s

    def to_summary(self) -> dict:
        """Compact, aggregate-only view (no account names or raw descriptions) for the AI coach."""
        top_cats = sorted(((c, self.category_avg(c)) for c in self.cat_matrix), key=lambda x: -x[1])[:15]
        return {
            "period": f"{month_label(self.months[0])} – {month_label(self.months[-1])}",
            "months": self.n,
            "avg_monthly_income": round(self.avg_income, 2),
            "avg_monthly_spending": round(self.avg_spend, 2),
            "savings_rate": round(self.savings_rate, 4) if self.savings_rate is not None else None,
            "savings_rate_target": self.savings_target,
            "monthly": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in x.items()} for x in self.monthly],
            "top_categories_avg_per_month": [
                {"category": c, "avg": round(v, 2), "class": self.ledger.class_of(c),
                 "protected": self.ledger.is_protected(c)} for c, v in top_cats],
            "fixed_commitments_per_month": self.fixed_monthly(),
            "subscriptions": [{"merchant": r.merchant, "monthly": r.monthly_cost, "new": r.is_new,
                               "price_change": r.price_change}
                              for r in self.active_recurring if r.kind == "subscription"],
            "insights": [i.to_dict() for i in self.insights],
            "wins": [{"title": w.title, "detail": w.detail} for w in self.wins],
            "potential_monthly_savings": self.potential_monthly,
            "potential_one_time_recovery": self.potential_one_time,
        }


def _per(cadence: str) -> str:
    return {"weekly": "wk", "biweekly": "2 wks", "monthly": "mo", "quarterly": "qtr", "semiannual": "6 mo",
            "annual": "yr"}.get(cadence, "mo")
