"""Write reports to disk, export itemized CSVs, and email them."""

from __future__ import annotations

import csv
import json
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from finagent.config import Config
from finagent.models import Transaction
from finagent.reports.model import Report
from finagent.reports.render_html import render_html
from finagent.reports.render_md import render_markdown

CSV_COLUMNS = ["date", "merchant", "payee", "category", "group", "class", "account", "spent", "amount", "memo",
               "flags", "source"]


def export_csv(txns: list[Transaction], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_COLUMNS)
        for t in sorted(txns, key=lambda x: (x.date, x.merchant)):
            w.writerow([t.date.isoformat(), t.merchant, t.payee, t.category, t.category_group, t.cls, t.account,
                        f"{t.outflow:.2f}", f"{t.amount:.2f}", t.memo, "; ".join(sorted(t.flags)), t.source])
    return path


def write_report(report: Report, out_dir: Path, formats: list[str]) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    if "html" in formats:
        paths["html"] = out_dir / f"{report.slug}.html"
        paths["html"].write_text(render_html(report), encoding="utf-8")
    if "md" in formats:
        paths["md"] = out_dir / f"{report.slug}.md"
        paths["md"].write_text(render_markdown(report), encoding="utf-8")
    if "json" in formats:
        paths["json"] = out_dir / f"{report.slug}.json"
        paths["json"].write_text(json.dumps(report.to_json(), indent=2, default=str), encoding="utf-8")
    if report.itemized:
        paths["csv"] = export_csv(report.itemized, out_dir / f"{report.slug}-itemized.csv")
    return paths


class EmailError(RuntimeError):
    pass


def email_report(report: Report, config: Config, attachments: list[Path] | None = None) -> list[str]:
    ec = config["email"]
    to = [a for a in (ec.get("to") or []) if a]
    if not to:
        raise EmailError("email.to is empty in config.toml")
    sender = ec.get("from_addr") or ec.get("username")
    if not sender:
        raise EmailError("Set email.from_addr (or email.username) in config.toml")
    msg = EmailMessage()
    msg["Subject"] = f"{ec.get('subject_prefix', '[Budget]')} {report.title} — {report.period_label}".strip()
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg.set_content(render_markdown(report))
    msg.add_alternative(render_html(report), subtype="html")
    for p in attachments or []:
        msg.add_attachment(p.read_bytes(), maintype="text", subtype="csv", filename=p.name)

    host, port = ec.get("smtp_host"), int(ec.get("smtp_port", 587))
    password = config.smtp_password()
    username = ec.get("username")
    if username and not password:
        raise EmailError("Set the FINAGENT_SMTP_PASSWORD environment variable (an app password, not your login)")
    ctx = ssl.create_default_context()
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=30, context=ctx)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
        with server:
            server.ehlo()
            if port != 465 and ec.get("use_starttls", True):
                server.starttls(context=ctx)
                server.ehlo()
            if username:
                server.login(username, password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as err:
        raise EmailError(f"SMTP login failed ({err.smtp_code}). Gmail and Outlook.com need an app password; "
                         f"some Microsoft 365 organizations disable SMTP sign-in entirely.") from None
    except (smtplib.SMTPException, OSError) as err:
        raise EmailError(f"Couldn't send email via {host}:{port}: {err}") from None
    return to
