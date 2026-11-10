import json
from datetime import date
from types import SimpleNamespace

from finagent.ai import coach_note
from finagent.cli import main
from finagent.insights import Analysis
from finagent.ledger import Ledger
from finagent.reports.builders import build_monthly, build_review, build_weekly
from finagent.reports.render_html import render_html
from finagent.reports.render_md import render_markdown
from finagent.sources.demo import generate
from tests.conftest import TODAY


def test_all_reports_render(demo, cfg):
    ledger, A = demo
    review = build_review(ledger, A)
    html, md = render_html(review), render_markdown(review)
    assert "30-day action plan" in html and "Every expense, itemized" in html
    assert "Dining Out" in md and "| Category |" in md
    json.dumps(review.to_json(), default=str)

    txns, budgets = generate(TODAY)
    lm = Ledger(txns, budgets, cfg, date(2026, 11, 1))
    monthly = build_monthly(lm, "2026-10", Analysis(lm, lm.available_months(6)))
    assert "October 2026" in monthly.headline and "Budget vs. actual" in render_html(monthly)

    lw = Ledger(txns, budgets, cfg, date(2026, 11, 9))
    weekly = build_weekly(lw, date(2026, 11, 2), date(2026, 11, 8), Analysis(lw, lw.available_months(6)))
    wh = render_html(weekly)
    assert "Every expense this week" in wh and "Projected month-end" in wh
    assert "Lakeside Steakhouse" in wh  # the large purchase planted last week
    assert len(weekly.itemized) > 10


def test_weekly_works_without_complete_months(cfg):
    txns, budgets = generate(TODAY)
    recent = [t for t in txns if t.date >= date(2026, 11, 1)]
    lw = Ledger(recent, [], cfg, TODAY)
    rep = build_weekly(lw, date(2026, 11, 2), date(2026, 11, 8), None)
    assert "Nov 2" in rep.period_label and render_html(rep)


def test_coach_note_with_fake_client(cfg):
    note = {"headline": "Solid giving, dining is the lever.", "summary": "s", "encouragement": "e",
            "discussion_question": "q?", "priorities": [{"action": "a", "why": "w", "impact": "$500/mo"}] * 4}
    seen = {}

    class Msgs:
        def create(self, **kw):
            seen.update(kw)
            return SimpleNamespace(stop_reason="end_turn",
                                   content=[SimpleNamespace(type="text", text=json.dumps(note))])

    client = SimpleNamespace(beta=SimpleNamespace(messages=Msgs()))
    out = coach_note({"x": 1}, "review", cfg, client=client)
    assert out["headline"].startswith("Solid") and len(out["priorities"]) == 3
    assert seen["model"] == "claude-opus-5-5" and seen["fallbacks"] == "default"
    assert seen["output_config"]["format"]["type"] == "json_schema"

    class Refuse:
        def create(self, **kw):
            return SimpleNamespace(stop_reason="refusal", content=[])

    assert coach_note({}, "weekly", cfg, client=SimpleNamespace(beta=SimpleNamespace(messages=Refuse()))) is None


def _write_cfg(tmp_path, extra=""):
    p = tmp_path / "config.toml"
    p.write_text(f'[household]\nname = "CLI Test"\n[source]\ntype = "csv"\n[report]\nformats = ["html","md","json"]\n{extra}')
    return p


def test_cli_demo_end_to_end(tmp_path, capsys):
    cfg = _write_cfg(tmp_path)
    assert main(["--config", str(cfg), "--today", TODAY.isoformat(), "demo", "--out", str(tmp_path / "out")]) == 0
    names = {p.name for p in (tmp_path / "out").iterdir()}
    for expected in ("review-2026-05-to-2026-10.html", "weekly-2026-11-02.md", "monthly-2026-10.json",
                     "monthly-2026-10-itemized.csv"):
        assert expected in names
    assert (tmp_path / "data" / "demo.db").exists()
    assert not (tmp_path / "data" / "finance.db").exists()  # demo never touches real data


def test_cli_import_itemize_subscriptions_review(tmp_path, capsys):
    cfg = _write_cfg(tmp_path)
    txns, _ = generate(TODAY)
    csv_path = tmp_path / "export.csv"
    lines = ["Date,Merchant,Category,Account,Original Statement,Notes,Amount,Tags"]
    for t in txns:
        cat = "Transfer" if t.is_transfer else t.category
        lines.append(f'{t.date.isoformat()},"{t.payee}","{cat}","{t.account}","{t.payee}",,{t.amount:.2f},')
    csv_path.write_text("\n".join(lines))
    base = ["--config", str(cfg), "--today", TODAY.isoformat()]
    assert main(base + ["import", str(csv_path)]) == 0
    assert main(base + ["import", str(csv_path)]) == 0  # idempotent
    out = capsys.readouterr().out
    assert "monarch format" in out and f"0 new, {len(txns)} already imported" in out

    assert main(base + ["itemize", "--months", "3", "--csv", str(tmp_path / "items.csv")]) == 0
    out = capsys.readouterr().out
    assert "Dining Out" in out and (tmp_path / "items.csv").read_text().startswith("date,merchant")

    assert main(base + ["subscriptions"]) == 0
    out = capsys.readouterr().out
    assert "Netflix" in out and "↑ from $15.49" in out and "Looks cancelled: Peacock" in out

    assert main(base + ["review", "--months", "3", "--out", str(tmp_path / "r")]) == 0
    assert any(p.suffix == ".html" for p in (tmp_path / "r").iterdir())
    assert main(base + ["monthly", "--month", "2026-11"]) == 2  # month not over → clear error


def test_cli_without_data_gives_guidance(tmp_path, capsys):
    cfg = _write_cfg(tmp_path)
    assert main(["--config", str(cfg), "review"]) == 2
    assert "finagent import" in capsys.readouterr().err


def test_email_report_builds_multipart_message(demo, cfg, tmp_path, monkeypatch):
    import smtplib

    from finagent.output import email_report

    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent["server"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            pass

        def starttls(self, context=None):
            sent["tls"] = True

        def login(self, user, password):
            sent["login"] = (user, password)

        def send_message(self, msg):
            sent["msg"] = msg

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("FINAGENT_SMTP_PASSWORD", "app-password")
    cfg["email"].update({"username": "me@example.com", "to": ["me@example.com", "spouse@example.com"]})
    ledger, A = demo
    rep = build_review(ledger, A)
    csv_path = tmp_path / "items.csv"
    csv_path.write_text("date,merchant\n")
    assert email_report(rep, cfg, [csv_path]) == ["me@example.com", "spouse@example.com"]
    msg = sent["msg"]
    assert sent["tls"] and sent["login"] == ("me@example.com", "app-password")
    assert msg["Subject"].startswith("[Budget] 6-month budget review")
    types = [part.get_content_type() for part in msg.walk()]
    assert "text/html" in types and "text/plain" in types and "text/csv" in types


def test_budget_suggest_save_writes_config_once(tmp_path, capsys):
    import tomllib

    from finagent.config import EXAMPLE_CONFIG
    from finagent.store import Store
    cfg = tmp_path / "config.toml"
    cfg.write_text(EXAMPLE_CONFIG.read_text())  # what `finagent init` creates — already has a [budgets] table
    txns, _ = generate(TODAY)
    with Store(tmp_path / "data" / "finance.db") as store:
        store.upsert_transactions(txns)
    base = ["--config", str(cfg), "--today", TODAY.isoformat(), "budget-suggest"]
    assert main(base) == 0
    assert cfg.read_text() == EXAMPLE_CONFIG.read_text()  # preview only
    assert main(base + ["--save"]) == 0
    assert main(base + ["--save", "--trim", "20"]) == 0  # running again replaces, never duplicates
    text = cfg.read_text()
    assert text.count("[budgets]") == 1 and (tmp_path / "config.toml.bak").exists()
    data = tomllib.loads(text)
    assert data["budgets"]["Mortgage"] == 2150 and "Irregular Expenses" in data["budgets"]
    assert data["categories"]["force_merchant_rules"] is False  # the rest of the file is intact
    assert "-20%" in capsys.readouterr().out


def test_irregular_fund_is_set_aside_not_unused(cfg):
    from finagent.categorize import category_class
    assert category_class("Irregular Expenses", "Planning") == "savings"
    txns, budgets = generate(TODAY)
    from finagent.models import Budget
    budgets += [Budget(month=mo, category="Irregular Expenses", amount=425) for mo in {b.month for b in budgets}]
    ledger = Ledger(txns, budgets, cfg, TODAY)
    A = Analysis(ledger, ledger.available_months(6))
    unused = next((i for i in A.insights if i.key == "reallocate"), None)
    assert unused is None or "Irregular" not in unused.detail  # set aside, not "unused"
    fund = next(i for i in A.insights if i.key == "sinking-fund")
    assert "Irregular Expenses fund" in fund.title and "Create" not in fund.action


def test_budget_set_and_budgets(tmp_path, capsys):
    import tomllib

    from finagent.config import EXAMPLE_CONFIG
    from finagent.store import Store
    cfg = tmp_path / "config.toml"
    cfg.write_text(EXAMPLE_CONFIG.read_text())
    txns, _ = generate(TODAY)
    with Store(tmp_path / "data" / "finance.db") as store:
        store.upsert_transactions(txns)
    base = ["--config", str(cfg), "--today", TODAY.isoformat()]
    assert main(base + ["budgets"]) == 0
    assert "No budgets saved yet" in capsys.readouterr().out
    assert main(base + ["budget-suggest", "--save"]) == 0
    capsys.readouterr()

    # Adjust two, add one (any capitalisation, "$" and commas fine), remove one
    assert main(base + ["budget-set", "dining out=$900", "Groceries=1,100", "Clothing=0",
                        "Date Night=120", "--new"]) == 0
    out = capsys.readouterr().out
    assert "Dining Out: $925 → $900" in out and "Groceries: $1,175 → $1,100" in out
    assert "Clothing: removed" in out and "Date Night: $120 (new)" in out and "leaves" in out
    data = tomllib.loads(cfg.read_text())["budgets"]
    assert data["Dining Out"] == 900 and data["Groceries"] == 1100 and data["Date Night"] == 120
    assert "Clothing" not in data and cfg.read_text().count("[budgets]") == 1
    assert "# avg" in cfg.read_text()  # comments preserved

    # Typos are caught instead of silently creating a new budget
    assert main(base + ["budget-set", "Dinning Out=800"]) == 2
    assert 'Did you mean "Dining Out"' in capsys.readouterr().err
    assert main(base + ["budgets"]) == 0
    assert "Dining Out" in capsys.readouterr().out
