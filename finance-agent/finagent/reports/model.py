"""The report document: a title, a headline, and an ordered list of typed sections.

Builders produce a Report; renderers turn it into HTML, Markdown, or JSON. Keeping
the content separate from presentation means every format says the same thing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from finagent.insights import Insight, Win
from finagent.models import Transaction


@dataclass
class Report:
    kind: str  # weekly | monthly | review
    title: str
    period_label: str
    start: date
    end: date
    headline: str
    household: str
    currency: str
    sections: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)  # aggregate-only data for the AI coach
    coach: dict[str, Any] | None = None
    itemized: list[Transaction] = field(default_factory=list)  # for CSV export
    generated: datetime = field(default_factory=datetime.now)

    @property
    def slug(self) -> str:
        if self.kind == "weekly":
            return f"weekly-{self.start.isoformat()}"
        if self.kind == "monthly":
            return f"monthly-{self.start.strftime('%Y-%m')}"
        return f"review-{self.start.strftime('%Y-%m')}-to-{self.end.strftime('%Y-%m')}"

    def add(self, section_type: str, **kwargs: Any) -> None:
        self.sections.append({"type": section_type, **kwargs})

    def to_json(self) -> dict[str, Any]:
        def enc(v: Any) -> Any:
            if isinstance(v, Transaction):
                return {"date": v.date.isoformat(), "merchant": v.merchant, "payee": v.payee,
                        "category": v.category, "group": v.category_group, "account": v.account,
                        "amount": v.amount, "flags": sorted(v.flags)}
            if isinstance(v, Insight):
                return v.to_dict()
            if isinstance(v, Win):
                return {"title": v.title, "detail": v.detail}
            if isinstance(v, (date, datetime)):
                return v.isoformat()
            if isinstance(v, dict):
                return {k: enc(x) for k, x in v.items()}
            if isinstance(v, (list, tuple)):
                return [enc(x) for x in v]
            return v

        return {"kind": self.kind, "title": self.title, "period": self.period_label,
                "start": self.start.isoformat(), "end": self.end.isoformat(), "headline": self.headline,
                "generated": self.generated.isoformat(timespec="seconds"), "coach": self.coach,
                "sections": enc(self.sections), "summary": self.summary}
