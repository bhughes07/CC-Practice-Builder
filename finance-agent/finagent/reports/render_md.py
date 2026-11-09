"""Render a Report as GitHub-flavored Markdown (terminal-, notes-, and Claude-friendly)."""

from __future__ import annotations

from finagent.insights import Insight
from finagent.models import Transaction
from finagent.reports.model import Report
from finagent.util import fmt_date, fmt_datetime, money


def _esc(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _cell(c: object) -> str:
    if isinstance(c, list):
        return ", ".join(x for x in (_cell(i) for i in c) if x)
    if isinstance(c, dict):
        return _esc(c.get("text", ""))
    return _esc(c)


def _table(columns: list[str], rows: list[list], align: list[str] | None = None) -> str:
    align = align or ["l"] * len(columns)
    head = "| " + " | ".join(_esc(c) for c in columns) + " |"
    sep = "|" + "|".join("---:" if a == "r" else ":---" for a in align) + "|"
    body = ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join([head, sep, *body])


def _bar(pct: float, width: int = 20) -> str:
    n = max(0, min(width, round(pct / 100 * width)))
    return "█" * n + "░" * (width - n)


def _impact(i: Insight, cur: str) -> str:
    bits = []
    if i.monthly_impact >= 0.5:
        bits.append(f"≈ {money(i.monthly_impact, cur, cents=i.monthly_impact < 100)}/mo "
                    f"({money(i.annual_impact, cur, cents=False)}/yr)")
    if i.one_time >= 0.5:
        bits.append(f"{money(i.one_time, cur)} to recover")
    return " · ".join(bits)


def _txn_line(t: Transaction, cur: str, reason: str = "") -> list:
    flags = sorted(t.flags)
    if reason and reason not in flags:
        flags.insert(0, reason)
    return [fmt_date(t.date), t.merchant or t.payee, t.category or "Uncategorized", money(t.outflow, cur),
            ", ".join(flags)]


def render_markdown(rep: Report) -> str:
    cur = rep.currency
    out = [f"# {rep.title} — {rep.period_label}", f"_{rep.household}_", "", f"**{rep.headline}**", ""]
    for idx, s in enumerate(rep.sections):
        t = s["type"]
        title = s.get("title")
        if t == "kpis":
            out.append(_table(["", "", ""], [[k["label"], f"**{k['value']}**", k.get("sub", "")] for k in s["items"]],
                              ["l", "r", "l"]))
        elif t == "callout":
            out.append(f"> **{s['title']}** — {s['body']}")
        elif t == "insights":
            out.append(f"## {title}")
            if s.get("total_monthly") or s.get("total_one_time"):
                parts = []
                if s.get("total_monthly"):
                    parts.append(f"**{money(s['total_monthly'], cur, cents=False)}/mo** "
                                 f"({money(s['total_monthly'] * 12, cur, cents=False)}/yr) if you hit every target")
                if s.get("total_one_time"):
                    parts.append(f"**{money(s['total_one_time'], cur)}** to recover")
                out.append("Potential: " + " · ".join(parts) + "\n")
            for n, i in enumerate(s["items"], 1):
                impact = _impact(i, cur)
                out.append(f"### {n}. {i.title}" if not s.get("compact") else f"- **{i.title}**")
                if s.get("compact"):
                    out.append(f"  {i.detail} → {i.action}" + (f" _({impact})_" if impact else ""))
                    continue
                out.append(f"`{i.severity} priority`" + (f" · **{impact}**" if impact else ""))
                out.append("")
                out.append(i.detail)
                out.append("")
                out.append(f"**Do this:** {i.action}")
                for ev in i.evidence[:6]:
                    out.append(f"- {ev}")
                out.append("")
            if s.get("note"):
                out.append(f"_{s['note']}_")
        elif t == "bars":
            out.append(f"## {title}")
            top = max((r["value"] for r in s["rows"]), default=1) or 1
            out.append(_table(["", "", "Amount", ""],
                              [[r["label"], f"`{_bar(r['value'] / top * 100)}`", r["display"], r.get("sub", "")]
                               for r in s["rows"]], ["l", "l", "r", "l"]))
            if s.get("note"):
                out.append(f"\n_{s['note']}_")
        elif t == "columns":
            out.append(f"## {title}")
            rows = []
            for p in s["points"]:
                inc, sp = p["values"]
                rows.append([p["label"], money(inc, cur, cents=False), money(sp, cur, cents=False),
                             money(inc - sp, cur, cents=False, signed=True)])
            out.append(_table(["Month", s["series"][0], s["series"][1], "Kept"], rows, ["l", "r", "r", "r"]))
        elif t == "table":
            out.append(f"## {title}")
            out.append(_table(s["columns"], s["rows"], s.get("align")))
            if s.get("note"):
                out.append(f"\n_{s['note']}_")
        elif t == "transactions":
            out.append(f"## {title}")
            if s["rows"]:
                reasons = s.get("reasons") or {}
                out.append(_table(["Date", "Merchant", "Category", "Amount", "Flags"],
                                  [_txn_line(x, cur, reasons.get(x.id, "")) for x in s["rows"]],
                                  ["l", "l", "l", "r", "l"]))
            else:
                out.append("_Nothing here._")
            if s.get("note"):
                out.append(f"\n_{s['note']}_")
        elif t == "itemization":
            out.append(f"## {title}")
            out.append(f"**{money(s['total'], cur)}** across {s['count']} transactions. {s.get('note', '')}\n")
            for g in s["groups"]:
                out.append(f"### {g['name']} — {money(g['total'], cur)}")
                for c in g["categories"]:
                    out.append(f"**{c['name']}** — {money(c['total'], cur)} ({c['count']})\n")
                    out.append(_table(["Date", "Merchant", "Account", "Amount"],
                                      [[fmt_date(x.date), x.merchant or x.payee, x.account, money(x.outflow, cur)]
                                       for x in c["transactions"]], ["l", "l", "l", "r"]))
                    out.append("")
        elif t == "wins":
            out.append(f"## {title}")
            out.extend(f"- ✅ **{w.title}.** {w.detail}" for w in s["items"])
        elif t == "list":
            out.append(f"## {title}")
            out.extend((f"{n}. {x}" if s.get("ordered") else f"- {x}") for n, x in enumerate(s["items"], 1))
        out.append("")
        if idx == 0 and rep.coach:
            out.extend(_coach(rep.coach))
    out.append("---")
    out.append(f"_Generated {fmt_datetime(rep.generated)} by finagent. Transfers between your own "
               f"accounts and card payments are excluded._")
    return "\n".join(out) + "\n"


def _coach(c: dict) -> list[str]:
    lines = ["## Coach's note", f"**{c.get('headline', '')}**", "", c.get("summary", ""), ""]
    for n, p in enumerate(c.get("priorities", []), 1):
        why = f" — {p['why']}" if p.get("why") else ""
        imp = f" _({p['impact']})_" if p.get("impact") else ""
        lines.append(f"{n}. **{p.get('action', '')}**{why}{imp}")
    if c.get("encouragement"):
        lines += ["", c["encouragement"]]
    if c.get("discussion_question"):
        lines += ["", f"> **Talk about it:** {c['discussion_question']}"]
    lines += ["", "_Written by Claude from the aggregated numbers in this report — not financial advice._", ""]
    return lines
