"""Render a Report as a single self-contained, email-safe HTML document.

Layout is table-based with inline styles so it survives Outlook, Gmail, and
Apple Mail — and still looks good opened directly in a browser.
"""

from __future__ import annotations

import html
import re

from finagent.insights import Insight
from finagent.models import Transaction
from finagent.reports.model import Report
from finagent.util import fmt_date, fmt_datetime, money, money_auto

INK, MUTED, LINE, BG, CARD = "#18212b", "#5d6b79", "#e3e7ec", "#f3f5f7", "#ffffff"
ACCENT, ACCENT_SOFT = "#1d6b57", "#e7f2ee"
TONES = {
    "good": ("#0f7a4a", "#e6f4ec"), "bad": ("#b42318", "#fdecea"), "warn": ("#a15c07", "#fdf3e1"),
    "info": ("#1f5fbf", "#e9f0fb"), "accent": (ACCENT, ACCENT_SOFT), "": (INK, "#f1f3f5"),
}
SEVERITY = {"high": ("bad", "High"), "medium": ("warn", "Medium"), "low": ("info", "Low")}
BAR_NEUTRAL, INCOME_COLOR, SPEND_COLOR = "#8aa0b6", ACCENT, "#c27c2c"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def e(text: object) -> str:
    return html.escape(str(text), quote=True)


def md_inline(text: str) -> str:
    """Escape, then allow **bold** and _italic_."""
    s = e(text)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w])_(.+?)_(?![\w])", r"<em>\1</em>", s)
    return s


def pill(text: str, tone: str = "") -> str:
    fg, bg = TONES.get(tone, TONES[""])
    return (f'<span style="display:inline-block;padding:2px 8px;border-radius:999px;background:{bg};color:{fg};'
            f'font-size:12px;font-weight:600;line-height:18px;white-space:nowrap">{e(text)}</span>')


def bar(pct: float, color: str, height: int = 10) -> str:
    pct = max(0.0, min(100.0, pct))
    if pct < 0.5:
        return f'<div style="height:{height}px;line-height:{height}px;font-size:0">&nbsp;</div>'
    rest = '<td style="font-size:0;line-height:0">&nbsp;</td>' if pct < 99.5 else ""
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td width="{pct:.1f}%" bgcolor="{color}" style="background:{color};height:{height}px;'
            f'line-height:{height}px;font-size:0;border-radius:3px">&nbsp;</td>{rest}</tr></table>')


def section_card(title: str, body: str, note: str = "") -> str:
    head = (f'<div style="font-size:17px;font-weight:700;color:{INK};margin:0 0 12px">{e(title)}</div>'
            if title else "")
    foot = f'<div style="font-size:12px;color:{MUTED};margin-top:12px">{md_inline(note)}</div>' if note else ""
    return (f'<tr><td style="padding:0 0 16px"><table role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0" style="background:{CARD};border:1px solid {LINE};border-radius:12px">'
            f'<tr><td style="padding:20px 22px">{head}{body}{foot}</td></tr></table></td></tr>')


# --------------------------------------------------------------------------- section renderers

def r_kpis(s: dict, rep: Report) -> str:
    items = s["items"]
    width = 100 / max(1, len(items))
    cells = []
    for it in items:
        fg = TONES.get(it.get("tone", ""), TONES[""])[0]
        cells.append(
            f'<td class="kpi" width="{width:.0f}%" valign="top" style="padding:6px">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{CARD};'
            f'border:1px solid {LINE};border-radius:12px"><tr><td style="padding:14px 14px 12px">'
            f'<div style="font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:{MUTED};'
            f'font-weight:600">{e(it["label"])}</div>'
            f'<div style="font-size:24px;font-weight:700;color:{fg};margin:6px 0 2px;'
            f'font-variant-numeric:tabular-nums;white-space:nowrap">{e(it["value"])}</div>'
            f'<div style="font-size:12px;color:{MUTED};line-height:16px">{e(it.get("sub", ""))}</div>'
            f'</td></tr></table></td>')
    return (f'<tr><td style="padding:0 0 10px"><table role="presentation" class="kpis" width="100%" '
            f'cellpadding="0" cellspacing="0" style="margin:0 -6px;width:calc(100% + 12px)"><tr>{"".join(cells)}'
            f'</tr></table></td></tr>')


def r_callout(s: dict, rep: Report) -> str:
    fg, bg = TONES.get(s.get("tone", "info"), TONES["info"])
    return (f'<tr><td style="padding:0 0 16px"><table role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0" style="background:{bg};border-radius:12px;border-left:4px solid {fg}"><tr>'
            f'<td style="padding:14px 18px"><div style="font-weight:700;color:{fg};font-size:15px">{e(s["title"])}</div>'
            f'<div style="color:{INK};font-size:14px;line-height:21px;margin-top:4px">{md_inline(s["body"])}</div>'
            f'</td></tr></table></td></tr>')


def _impact(i: Insight, cur: str) -> str:
    bits = []
    if i.monthly_impact >= 0.5:
        bits.append(pill(f"{money_auto(i.monthly_impact, cur)}/mo · "
                         f"{money(i.annual_impact, cur, cents=False)}/yr", "accent"))
    if i.one_time >= 0.5:
        bits.append(pill(f"{money(i.one_time, cur)} to recover", "good"))
    if not bits and i.kind == "protect":
        bits.append(pill("plan ahead", "info"))
    if not bits and i.kind == "opportunity":
        bits.append(pill("opportunity", "info"))
    if not bits and i.kind == "goal":
        bits.append(pill("your goal", "accent"))
    return " ".join(bits)


def r_insights(s: dict, rep: Report) -> str:
    cur = rep.currency
    compact = s.get("compact", False)
    blocks = []
    for n, i in enumerate(s["items"], 1):
        tone, label = SEVERITY.get(i.severity, ("info", i.severity))
        fg = TONES[tone][0]
        evidence = ""
        if i.evidence and not compact:
            evidence = ('<ul style="margin:8px 0 0;padding-left:18px;color:' + MUTED + ';font-size:13px;'
                        'line-height:19px">' + "".join(f"<li>{e(x)}</li>" for x in i.evidence[:6]) + "</ul>")
        number = "" if compact else f'<span style="color:{MUTED};font-weight:600">{n}.&nbsp;</span>'
        blocks.append(
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 12px;'
            f'border-left:4px solid {fg};background:#fbfcfd;border-radius:8px"><tr><td style="padding:12px 14px">'
            f'<div style="font-size:15px;font-weight:700;color:{INK};line-height:21px">{number}{e(i.title)}</div>'
            f'<div style="margin:6px 0">{pill(label + " priority", tone)} {_impact(i, cur)}</div>'
            f'<div style="font-size:14px;color:{INK};line-height:21px">{md_inline(i.detail)}</div>'
            f'<div style="font-size:14px;line-height:21px;margin-top:8px;padding:8px 10px;background:{ACCENT_SOFT};'
            f'border-radius:6px;color:{INK}"><strong style="color:{ACCENT}">Do this:</strong> {md_inline(i.action)}</div>'
            f'{evidence}</td></tr></table>')
    intro = ""
    if s.get("total_monthly") or s.get("total_one_time"):
        parts = []
        if s.get("total_monthly"):
            parts.append(f"<strong>{e(money(s['total_monthly'], cur, cents=False))}/mo</strong> "
                         f"({e(money(s['total_monthly'] * 12, cur, cents=False))}/yr) if you hit every target")
        if s.get("total_one_time"):
            parts.append(f"<strong>{e(money(s['total_one_time'], cur))}</strong> to recover")
        intro = f'<div style="font-size:14px;color:{INK};margin:-4px 0 14px">Potential: {" · ".join(parts)}</div>'
    return section_card(s.get("title", ""), intro + "".join(blocks), s.get("note", ""))


def r_bars(s: dict, rep: Report) -> str:
    rows = s["rows"]
    if not rows:
        return ""
    top = max(r["value"] for r in rows) or 1
    out = []
    for r in rows:
        tone = r.get("tone", "")
        color = TONES[tone][0] if tone in ("bad", "good", "accent", "warn") else BAR_NEUTRAL
        sub = f'<div style="font-size:12px;color:{MUTED};line-height:16px">{e(r["sub"])}</div>' if r.get("sub") else ""
        out.append(
            f'<tr><td width="36%" valign="middle" style="padding:6px 10px 6px 0;font-size:14px;color:{INK}">'
            f'{e(r["label"])}{sub}</td>'
            f'<td width="44%" valign="middle" style="padding:6px 0">{bar(r["value"] / top * 100, color)}</td>'
            f'<td width="20%" align="right" valign="middle" style="padding:6px 0 6px 10px;font-size:14px;'
            f'font-weight:600;color:{INK};font-variant-numeric:tabular-nums;white-space:nowrap">{e(r["display"])}</td></tr>')
    body = f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{"".join(out)}</table>'
    return section_card(s.get("title", ""), body, s.get("note", ""))


def r_columns(s: dict, rep: Report) -> str:
    pts = s["points"]
    if not pts:
        return ""
    top = max(max(p["values"]) for p in pts) or 1
    height = 140
    colors = [INCOME_COLOR, SPEND_COLOR]
    cells = []
    for p in pts:
        bars = []
        for v, c in zip(p["values"], colors):
            h = max(2, int(round(v / top * height)))
            bars.append(f'<td valign="bottom" style="padding:0 2px"><table role="presentation" cellpadding="0" '
                        f'cellspacing="0"><tr><td height="{h}" width="16" bgcolor="{c}" style="height:{h}px;'
                        f'width:16px;background:{c};font-size:0;line-height:0;border-radius:3px 3px 0 0">&nbsp;'
                        f'</td></tr></table></td>')
        net = p["values"][0] - p["values"][1]
        tone = TONES["good" if net >= 0 else "bad"][0]
        cells.append(
            f'<td align="center" valign="bottom" style="padding:0 4px">'
            f'<table role="presentation" cellpadding="0" cellspacing="0"><tr><td height="{height}" valign="bottom" '
            f'style="height:{height}px"><table role="presentation" cellpadding="0" cellspacing="0"><tr>{"".join(bars)}'
            f'</tr></table></td></tr></table>'
            f'<div style="font-size:12px;color:{INK};font-weight:600;margin-top:6px">{e(p["label"])}</div>'
            f'<div style="font-size:11px;color:{tone};font-variant-numeric:tabular-nums">'
            f'{e(money(net, rep.currency, cents=False, signed=True))}</div></td>')
    legend = " &nbsp; ".join(
        f'<span style="display:inline-block;width:10px;height:10px;background:{c};border-radius:2px;'
        f'vertical-align:middle"></span> <span style="font-size:12px;color:{MUTED};vertical-align:middle">{e(name)}'
        f'</span>' for name, c in zip(s["series"], colors))
    body = (f'<div style="margin:-6px 0 12px">{legend} &nbsp; <span style="font-size:12px;color:{MUTED}">'
            f'· number under each month = kept (or overspent)</span></div>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{"".join(cells)}</tr>'
            f'<tr><td colspan="{len(pts)}" style="border-top:1px solid {LINE};font-size:0;line-height:0">&nbsp;</td></tr>'
            f'</table>')
    return section_card(s.get("title", ""), body, s.get("note", ""))


def _cell_html(c: object) -> str:
    if isinstance(c, list):
        return " ".join(_cell_html(x) for x in c)
    if isinstance(c, dict):
        tone = c.get("tone", "")
        if not c.get("text"):
            return ""
        if tone in ("warn",) or (tone == "" and c.get("text") in ("varies",)):
            return pill(c["text"], tone)
        fg = TONES.get(tone, TONES[""])[0]
        return f'<span style="color:{fg};font-weight:{600 if tone else 400}">{e(c["text"])}</span>'
    return e(c)


def r_table(s: dict, rep: Report) -> str:
    cols, rows, align = s["columns"], s["rows"], s.get("align", [])
    if not rows:
        return ""
    th = "".join(
        f'<th align="{"right" if (align[i:i + 1] or ["l"])[0] == "r" else "left"}" style="padding:8px 8px;'
        f'font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:{MUTED};font-weight:600;'
        f'border-bottom:1px solid {LINE};white-space:nowrap">{e(c)}</th>' for i, c in enumerate(cols))
    trs = []
    for n, row in enumerate(rows):
        tds = "".join(
            f'<td align="{"right" if (align[i:i + 1] or ["l"])[0] == "r" else "left"}" style="padding:7px 8px;'
            f'font-size:13px;color:{INK};border-bottom:1px solid #f0f2f4;font-variant-numeric:tabular-nums;'
            f'{"white-space:nowrap;" if (align[i:i + 1] or ["l"])[0] == "r" else ""}">{_cell_html(c)}</td>'
            for i, c in enumerate(row))
        trs.append(f'<tr style="background:{"#fbfcfd" if n % 2 else CARD}">{tds}</tr>')
    body = (f'<div style="overflow-x:auto"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            f'<tr>{th}</tr>{"".join(trs)}</table></div>')
    return section_card(s.get("title", ""), body, s.get("note", ""))


def _flags(t: Transaction, reason: str = "") -> str:
    out = []
    if reason and reason not in t.flags:
        out.append(pill(reason, "warn"))
    for f in sorted(t.flags):
        tone = {"possible duplicate": "bad", "fee": "bad", "unusual": "warn", "recurring": ""}.get(f, "")
        out.append(pill(f, tone))
    return " ".join(out)


def _txn_rows(txns: list[Transaction], cur: str, reasons: dict | None = None, show_category: bool = True) -> str:
    rows = []
    for n, t in enumerate(txns):
        amt_color = TONES["good"][0] if t.outflow < 0 else INK
        cat = (f'<div style="font-size:12px;color:{MUTED}">{e(t.category or "Uncategorized")}'
               f'{" · " + e(t.account) if t.account else ""}</div>') if show_category else ""
        rows.append(
            f'<tr style="background:{"#fbfcfd" if n % 2 else CARD}">'
            f'<td valign="top" style="padding:7px 8px;font-size:13px;color:{MUTED};white-space:nowrap">'
            f'{e(fmt_date(t.date))}</td>'
            f'<td valign="top" style="padding:7px 8px;font-size:13px;color:{INK}">{e(t.merchant or t.payee)}{cat}</td>'
            f'<td valign="top" style="padding:7px 8px;font-size:12px">{_flags(t, (reasons or {}).get(t.id, ""))}</td>'
            f'<td valign="top" align="right" style="padding:7px 8px;font-size:13px;font-weight:600;color:{amt_color};'
            f'font-variant-numeric:tabular-nums;white-space:nowrap">{e(money(t.outflow, cur))}</td></tr>')
    return "".join(rows)


def r_transactions(s: dict, rep: Report) -> str:
    if not s["rows"]:
        return section_card(s.get("title", ""), f'<div style="color:{MUTED};font-size:14px">Nothing here.</div>')
    body = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            f'{_txn_rows(s["rows"], rep.currency, s.get("reasons"))}</table>')
    return section_card(s.get("title", ""), body, s.get("note", ""))


def r_itemization(s: dict, rep: Report) -> str:
    cur = rep.currency
    parts = []
    for g in s["groups"]:
        parts.append(
            f'<tr><td colspan="4" style="padding:14px 8px 6px;font-size:13px;font-weight:700;color:{ACCENT};'
            f'text-transform:uppercase;letter-spacing:.05em;border-bottom:2px solid {ACCENT_SOFT}">{e(g["name"])}'
            f'<span style="float:right;font-variant-numeric:tabular-nums">{e(money(g["total"], cur))}</span></td></tr>')
        for c in g["categories"]:
            parts.append(
                f'<tr><td colspan="4" style="padding:10px 8px 4px;font-size:14px;font-weight:700;color:{INK}">'
                f'{e(c["name"])} <span style="font-weight:400;color:{MUTED};font-size:12px">· {c["count"]} '
                f'item{"s" if c["count"] != 1 else ""}</span><span style="float:right;font-variant-numeric:tabular-nums">'
                f'{e(money(c["total"], cur))}</span></td></tr>')
            parts.append(_txn_rows(c["transactions"], cur, show_category=False))
    body = (f'<div style="font-size:14px;color:{INK};margin:-4px 0 6px"><strong>{e(money(s["total"], cur))}</strong> '
            f'across {s["count"]} transactions</div>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{"".join(parts)}</table>')
    return section_card(s.get("title", ""), body, s.get("note", ""))


def r_wins(s: dict, rep: Report) -> str:
    items = "".join(
        f'<tr><td valign="top" width="26" style="padding:6px 0;color:{TONES["good"][0]};font-size:16px">✓</td>'
        f'<td style="padding:6px 0;font-size:14px;color:{INK};line-height:20px"><strong>{e(w.title)}.</strong> '
        f'{e(w.detail)}</td></tr>' for w in s["items"])
    return section_card(s.get("title", ""), f'<table role="presentation" width="100%" cellpadding="0" '
                                            f'cellspacing="0">{items}</table>', s.get("note", ""))


def r_list(s: dict, rep: Report) -> str:
    tag = "ol" if s.get("ordered") else "ul"
    items = "".join(f'<li style="margin:0 0 10px">{md_inline(x)}</li>' for x in s["items"])
    body = (f'<{tag} style="margin:0;padding-left:20px;font-size:14px;line-height:21px;color:{INK}">{items}</{tag}>')
    return section_card(s.get("title", ""), body, s.get("note", ""))


def r_coach(coach: dict, rep: Report) -> str:
    pri = "".join(
        f'<li style="margin:0 0 10px"><strong>{e(p.get("action", ""))}</strong>'
        f'{" — " + e(p["why"]) if p.get("why") else ""}'
        f'{" " + pill(p["impact"], "accent") if p.get("impact") else ""}</li>'
        for p in coach.get("priorities", []))
    q = coach.get("discussion_question")
    body = (f'<div style="font-size:18px;font-weight:700;color:{INK};line-height:25px;margin-bottom:8px">'
            f'{e(coach.get("headline", ""))}</div>'
            f'<div style="font-size:14px;color:{INK};line-height:22px">{md_inline(coach.get("summary", ""))}</div>'
            + (f'<ol style="margin:12px 0 0;padding-left:20px;font-size:14px;line-height:21px;color:{INK}">{pri}</ol>'
               if pri else "")
            + (f'<div style="font-size:14px;color:{INK};line-height:21px;margin-top:10px">{md_inline(coach["encouragement"])}</div>'
               if coach.get("encouragement") else "")
            + (f'<div style="margin-top:12px;padding:10px 12px;background:{ACCENT_SOFT};border-radius:8px;font-size:14px;'
               f'color:{INK}"><strong style="color:{ACCENT}">Talk about it:</strong> {e(q)}</div>' if q else ""))
    return section_card("Coach's note", body,
                        "Written by Claude from the aggregated numbers in this report — not financial advice.")


RENDERERS = {
    "kpis": r_kpis, "callout": r_callout, "insights": r_insights, "bars": r_bars, "columns": r_columns,
    "table": r_table, "transactions": r_transactions, "itemization": r_itemization, "wins": r_wins, "list": r_list,
}


def render_html(rep: Report) -> str:
    body = []
    for i, s in enumerate(rep.sections):
        body.append(RENDERERS[s["type"]](s, rep))
        if i == 0 and rep.coach:  # coach's note right after the headline numbers
            body.append(r_coach(rep.coach, rep))
    if rep.coach and not rep.sections:
        body.append(r_coach(rep.coach, rep))
    generated = fmt_datetime(rep.generated)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>{e(rep.title)} · {e(rep.period_label)}</title>
<style>
  body {{ margin:0; padding:0; background:{BG}; }}
  table {{ border-collapse:collapse; }}
  @media (max-width: 640px) {{
    .kpis td.kpi {{ display:inline-block !important; width:50% !important; box-sizing:border-box; }}
    .wrap {{ padding:16px 10px !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background:{BG};font-family:{FONT};color:{INK}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG}">
<tr><td align="center" class="wrap" style="padding:28px 16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:760px;font-family:{FONT}">
<tr><td style="padding:0 0 18px">
  <div style="height:4px;width:56px;background:{ACCENT};border-radius:2px;margin-bottom:14px"></div>
  <div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:{MUTED};font-weight:600">{e(rep.household)} · {e(rep.title)}</div>
  <div style="font-size:28px;font-weight:800;color:{INK};margin:4px 0 8px;line-height:34px">{e(rep.period_label)}</div>
  <div style="font-size:16px;color:{INK};line-height:24px">{md_inline(rep.headline)}</div>
</td></tr>
{"".join(body)}
<tr><td style="padding:8px 4px 0;font-size:12px;color:{MUTED};line-height:18px">
  Generated {e(generated)} by finagent. Transfers between your own accounts and credit-card payments are excluded,
  so nothing is counted twice. Refunds reduce spending in their category.
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""
