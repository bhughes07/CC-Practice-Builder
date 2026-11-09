"""finagent command line.

    finagent demo                      see every report on realistic sample data
    finagent init                      create config.toml + folders
    finagent sync                      pull from YNAB / re-import CSV exports
    finagent import FILE.csv ...       import exports from any budgeting app or bank
    finagent itemize                   every expense, itemized, + CSV export
    finagent subscriptions             recurring charges and what they cost per year
    finagent review --months 6         3–6 month deep dive with ranked actions
    finagent weekly | monthly          the scheduled reports
    finagent run weekly|monthly        sync + report + email (for cron / Task Scheduler)
"""

from __future__ import annotations

import argparse
import glob
import shutil
import sys
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

from finagent import __version__
from finagent.config import EXAMPLE_CONFIG, Config, home_dir, load_config
from finagent.insights import Analysis
from finagent.ledger import Ledger
from finagent.output import EmailError, email_report, export_csv, write_report
from finagent.reports.builders import build_monthly, build_review, build_weekly
from finagent.reports.model import Report
from finagent.sources.csv_import import read_csv
from finagent.store import Store
from finagent.util import (
    add_months, fmt_date, fmt_range, last_complete_week, money, month_end, month_key, month_label, month_start,
    shift_month, week_start,
)


class CLIError(RuntimeError):
    pass


def _say(msg: str = "") -> None:
    print(msg)


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


# --------------------------------------------------------------------------- plumbing

def _today(args) -> date:
    return date.fromisoformat(args.today) if getattr(args, "today", None) else date.today()


def _store(cfg: Config, args) -> Store:
    path = Path(args.db) if getattr(args, "db", None) else cfg.resolve(cfg["storage"]["database"])
    return Store(path)


def _ledger(cfg: Config, store: Store, as_of: date) -> Ledger:
    if store.count() == 0:
        raise CLIError("No transactions yet. Run `finagent sync` (YNAB) or `finagent import <file.csv>` first — "
                       "or `finagent demo` to try it with sample data.")
    return Ledger(store.transactions(), store.budgets(), cfg, as_of)


def _out_dir(cfg: Config, args) -> Path:
    return Path(args.out) if getattr(args, "out", None) else cfg.resolve(cfg["report"]["output_dir"])


def _formats(cfg: Config, args) -> list[str]:
    if getattr(args, "format", None):
        return [f.strip() for f in args.format.split(",")]
    return list(cfg["report"]["formats"])


def _finish(rep: Report, cfg: Config, args, send_email: bool = False) -> dict:
    if getattr(args, "ai", False) or cfg["ai"].get("enabled"):
        from finagent.ai import coach_note
        rep.coach = coach_note(rep.summary, rep.kind, cfg)
    paths = write_report(rep, _out_dir(cfg, args), _formats(cfg, args))
    _say(f"\n{rep.title} — {rep.period_label}")
    _say("  " + rep.headline)
    actions = next((s for s in rep.sections if s["type"] == "insights" and not s.get("compact")), None)
    if actions and actions["items"]:
        _say("\n  Top actions:")
        for n, i in enumerate(actions["items"][:5], 1):
            val = (f"  ≈{money(i.monthly_impact, rep.currency, cents=False)}/mo" if i.monthly_impact >= 1 else
                   f"  {money(i.one_time, rep.currency)} back" if i.one_time else "")
            _say(f"   {n}. {i.title}{val}")
    _say("\n  Saved:")
    for kind, p in paths.items():
        _say(f"   {kind:5} {p}")
    if send_email or getattr(args, "email", False):
        try:
            to = email_report(rep, cfg, [paths["csv"]] if "csv" in paths else [])
            _say(f"\n  Emailed to {', '.join(to)}")
        except EmailError as err:
            _err(f"email not sent: {err}")
    if getattr(args, "open", False) and "html" in paths:
        webbrowser.open(paths["html"].resolve().as_uri())
    return paths


# --------------------------------------------------------------------------- sync / import

def do_sync(cfg: Config, store: Store, today: date) -> str:
    src = cfg["source"]["type"]
    history = int(cfg["source"].get("history_months", 12))
    since = add_months(date(today.year, today.month, 1), -history)
    if src == "ynab":
        from finagent.sources.ynab import YNABSource
        ynab = YNABSource(cfg.ynab_token() or "", cfg["source"].get("budget_id", "last-used"))
        txns, budgets = ynab.fetch(since, today)
        ins, upd, removed = store.replace_window("ynab", since, txns)
        store.upsert_budgets(budgets)
        store.set_meta("ynab:last_sync", datetime.now().isoformat(timespec="seconds"))
        return (f"YNAB: {len(txns)} transactions since {fmt_date(since, True)} ({ins} new, {upd} updated, "
                f"{removed} removed) and {len(budgets)} monthly budget lines.")
    if src == "csv":
        c = cfg["csv"]
        paths = sorted({p for pattern in c["paths"] for p in glob.glob(str(cfg.resolve(pattern)))})
        if not paths:
            return (f"No CSV files matched {c['paths']} (relative to {cfg.base_dir}). Export transactions from your "
                    f"budgeting app into the imports/ folder, or use `finagent import FILE.csv`.")
        lines = []
        for p in paths:
            res = read_csv(p, c.get("format", "auto"), c.get("sign", "auto"), c.get("date_order", "auto"))
            ins, upd = store.upsert_transactions(res.transactions)
            lines.append(f"  {Path(p).name}: {res.format} format, {len(res.transactions)} rows "
                         f"({ins} new){', amounts flipped' if res.sign_flipped else ''}"
                         f"{f', {res.skipped} skipped' if res.skipped else ''}")
        return "Imported CSV exports:\n" + "\n".join(lines)
    if src == "demo":
        from finagent.sources.demo import generate
        txns, budgets = generate(today)
        store.clear("demo")
        store.upsert_transactions(txns)
        store.upsert_budgets(budgets)
        return f"Demo data: {len(txns)} transactions."
    raise CLIError(f"Unknown source.type '{src}' (use ynab, csv, or demo)")


def cmd_sync(args, cfg: Config) -> int:
    with _store(cfg, args) as store:
        _say(do_sync(cfg, store, _today(args)))
        _status_line(store)
    return 0


def cmd_import(args, cfg: Config) -> int:
    with _store(cfg, args) as store:
        for f in args.files:
            res = read_csv(f, args.format or cfg["csv"]["format"], args.sign or cfg["csv"]["sign"],
                           cfg["csv"].get("date_order", "auto"), args.account)
            ins, upd = store.upsert_transactions(res.transactions)
            first = min((t.date for t in res.transactions), default=None)
            last = max((t.date for t in res.transactions), default=None)
            _say(f"{Path(f).name}: detected {res.format} format · {len(res.transactions)} transactions "
                 f"({ins} new, {upd} already imported)"
                 + (f" · {fmt_range(first, last)}" if first else "")
                 + (" · spending was exported as positive numbers, flipped" if res.sign_flipped else "")
                 + (f" · {res.skipped} rows skipped" if res.skipped else ""))
        _status_line(store)
    return 0


def _status_line(store: Store) -> None:
    b = store.date_bounds()
    if b:
        _say(f"Database: {store.count()} transactions, {fmt_date(b[0], True)} → {fmt_date(b[1], True)}.")


def cmd_status(args, cfg: Config) -> int:
    with _store(cfg, args) as store:
        _say(f"finagent {__version__}")
        _say(f"Config:   {cfg.path or '(defaults — run `finagent init`)'}")
        _say(f"Source:   {cfg['source']['type']}")
        _say(f"Database: {store.path}")
        _status_line(store)
        sources = store.conn.execute("SELECT source, COUNT(*) FROM transactions GROUP BY source").fetchall()
        for s, n in sources:
            _say(f"  {s}: {n}")
        _say(f"Budget lines: {len(store.budgets())}")
        last = store.get_meta("ynab:last_sync")
        if last:
            _say(f"Last YNAB sync: {last}")
    return 0


# --------------------------------------------------------------------------- reports

def _analysis(L: Ledger, n: int, required: bool = True) -> Analysis | None:
    months = L.available_months(n)
    if not months:
        if required:
            raise CLIError("Need at least one complete calendar month of transactions for this report.")
        return None
    if len(months) < n:
        print(f"note: only {len(months)} complete month(s) of data available (asked for {n}).", file=sys.stderr)
    return Analysis(L, months)


def cmd_review(args, cfg: Config) -> int:
    n = args.months or int(cfg["analysis"]["review_months"])
    if not 1 <= n <= 24:
        raise CLIError("--months must be between 1 and 24")
    with _store(cfg, args) as store:
        if args.sync:
            _say(do_sync(cfg, store, _today(args)))
        L = _ledger(cfg, store, _today(args))
        A = _analysis(L, n)
        _finish(build_review(L, A), cfg, args)
    return 0


def cmd_weekly(args, cfg: Config, send_email: bool = False) -> int:
    today = _today(args)
    if getattr(args, "week_of", None):
        ws = week_start(date.fromisoformat(args.week_of), cfg.week_starts_on)
        we = ws + timedelta(days=6)
    else:
        ws, we = last_complete_week(today, cfg.week_starts_on)
    with _store(cfg, args) as store:
        L = _ledger(cfg, store, min(today, we + timedelta(days=1)))
        A = _analysis(L, 6, required=False)
        _finish(build_weekly(L, ws, we, A), cfg, args, send_email)
    return 0


def cmd_monthly(args, cfg: Config, send_email: bool = False) -> int:
    today = _today(args)
    month = getattr(args, "month", None) or shift_month(month_key(today), -1)
    if month_end(month) >= today:
        raise CLIError(f"{month_label(month)} isn't over yet — monthly reports cover complete months.")
    with _store(cfg, args) as store:
        L = _ledger(cfg, store, min(today, month_end(month) + timedelta(days=1)))
        A = _analysis(L, int(cfg["analysis"]["review_months"]))
        if A.months[-1] != month:
            raise CLIError(f"No data for {month_label(month)}.")
        _finish(build_monthly(L, month, A), cfg, args, send_email)
    return 0


def cmd_run(args, cfg: Config) -> int:
    """Sync, build, save, and email (if enabled) — designed for schedulers."""
    with _store(cfg, args) as store:
        try:
            _say(do_sync(cfg, store, _today(args)))
        except Exception as err:  # keep going with what's already in the database
            _err(f"sync failed, reporting on existing data: {err}")
    send = bool(cfg["email"].get("enabled"))
    if args.report == "weekly":
        return cmd_weekly(args, cfg, send)
    if args.report == "monthly":
        return cmd_monthly(args, cfg, send)
    args.months, args.sync = None, False
    args.email = args.email or send
    return cmd_review(args, cfg)


# --------------------------------------------------------------------------- itemize / subscriptions

def _print_table(headers: list[str], rows: list[list[str]], align: str) -> None:
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    fmt = lambda row: "  ".join(  # noqa: E731
        str(c).rjust(w) if a == "r" else str(c).ljust(w) for c, w, a in zip(row, widths, align))
    _say(fmt(headers))
    _say("  ".join("-" * w for w in widths))
    for r in rows:
        _say(fmt(r))


def cmd_itemize(args, cfg: Config) -> int:
    today = _today(args)
    with _store(cfg, args) as store:
        L = _ledger(cfg, store, today)
        if args.month:
            start, end = month_start(args.month), month_end(args.month)
        elif args.start:
            start = date.fromisoformat(args.start)
            end = date.fromisoformat(args.end) if args.end else today
        else:
            months = L.available_months(args.months or int(cfg["analysis"]["review_months"]))
            if not months:
                raise CLIError("No complete months yet; use --from/--to.")
            start, end = month_start(months[0]), month_end(months[-1])
        txns = L.expenses_between(start, end)
        # Run detectors so the CSV carries flags (recurring / fee / duplicate / unusual).
        months_in = [m for m in L.available_months(24) if start <= month_start(m) and month_end(m) <= end]
        if months_in:
            Analysis(L, months_in)
        cur = L.currency
        by_cat = L.spend_by_category(start, end)
        total = sum(by_cat.values())
        _say(f"Expenses {fmt_range(start, end)}: {money(total, cur)} across {len(txns)} transactions\n")
        _print_table(["Category", "Group", "Items", "Total", "Share"],
                     [[c, L.group_of(c), str(sum(1 for t in txns if (t.category or 'Uncategorized') == c)),
                       money(v, cur), f"{v / total * 100:.1f}%" if total else "—"] for c, v in by_cat.items()],
                     "llrrr")
        if args.all:
            _say("")
            _print_table(["Date", "Merchant", "Category", "Account", "Amount", "Flags"],
                         [[t.date.isoformat(), (t.merchant or t.payee)[:32], t.category[:24], t.account[:16],
                           money(t.outflow, cur), ",".join(sorted(t.flags))] for t in txns], "llllrl")
        path = Path(args.csv) if args.csv else _out_dir(cfg, args) / f"itemized-{start.isoformat()}-to-{end.isoformat()}.csv"
        export_csv(txns, path)
        _say(f"\nFull itemized list (with merchant, category, account, flags): {path}")
    return 0


def cmd_subscriptions(args, cfg: Config) -> int:
    with _store(cfg, args) as store:
        L = _ledger(cfg, store, _today(args))
        A = _analysis(L, 6, required=False)
        if A is None:
            from finagent.recurring import detect_recurring
            recurring = detect_recurring(L.expenses, L.today)
        else:
            recurring = A.recurring
        cur = L.currency
        active = [r for r in recurring if r.active and (args.all or r.kind != "giving")]
        rows = []
        for r in active:
            note = "NEW" if r.is_new else ""
            if r.price_change > 0:
                note = (note + " " if note else "") + f"↑ from {money(r.previous_amount, cur)}"
            rows.append([r.merchant, r.kind, r.cadence, money(r.last_amount, cur), money(r.monthly_cost, cur),
                         money(r.annual_cost, cur, cents=False), fmt_date(r.next_date), note])
        _print_table(["Merchant", "Type", "Cadence", "Amount", "Per month", "Per year", "Next", "Notes"], rows,
                     "lllrrrll")
        subs = [r for r in active if r.kind == "subscription"]
        _say(f"\nSubscriptions: {len(subs)} · {money(sum(r.monthly_cost for r in subs), cur)}/mo · "
             f"{money(sum(r.annual_cost for r in subs), cur, cents=False)}/yr")
        _say(f"All recurring: {money(sum(r.monthly_cost for r in active), cur)}/mo")
        stopped = [r for r in recurring if not r.active and r.kind == "subscription"]
        if stopped:
            _say("Looks cancelled: " + ", ".join(f"{r.merchant} (last {fmt_date(r.last_date, True)})" for r in stopped))
    return 0


# --------------------------------------------------------------------------- setup / demo

def cmd_init(args, cfg: Config) -> int:
    root = Path(args.dir) if args.dir else home_dir()
    root.mkdir(parents=True, exist_ok=True)
    target = root / "config.toml"
    if target.exists():
        _say(f"{target} already exists — leaving it alone.")
    else:
        shutil.copy(EXAMPLE_CONFIG, target)
        _say(f"Created {target}")
    for d in ("data", "imports", "reports"):
        (root / d).mkdir(exist_ok=True)
    _say("""
Next steps:
  YNAB:   set source.type = "ynab" in config.toml, then
          export YNAB_TOKEN=...   (https://app.ynab.com/settings/developer)
          finagent sync
  Others: export transactions as CSV into imports/ (Monarch, Mint, Rocket Money, Copilot,
          YNAB, or your bank), then `finagent sync` — or `finagent import FILE.csv`
  Then:   finagent review --months 6 --open
""")
    return 0


def cmd_demo(args, cfg: Config) -> int:
    from finagent.sources.demo import generate
    today = _today(args)
    cfg["household"]["name"] = "Demo Household"
    db = cfg.resolve("data/demo.db")
    out = Path(args.out) if args.out else cfg.resolve(cfg["report"]["output_dir"]) / "demo"
    args.out = str(out)
    with Store(db) as store:
        store.clear()
        txns, budgets = generate(today)
        store.upsert_transactions(txns)
        store.upsert_budgets(budgets)
        _say(f"Generated {len(txns)} sample transactions for a demo household → {db}")
        L = _ledger(cfg, store, today)
        A = _analysis(L, 6)
        _finish(build_review(L, A), cfg, args)
        ws, we = last_complete_week(today, cfg.week_starts_on)
        Lw = _ledger(cfg, store, we + timedelta(days=1))
        _finish(build_weekly(Lw, ws, we, _analysis(Lw, 6, required=False)), cfg, args)
        month = shift_month(month_key(today), -1)
        Lm = _ledger(cfg, store, month_end(month) + timedelta(days=1))
        _finish(build_monthly(Lm, month, _analysis(Lm, 6)), cfg, args)
    _say(f"\nOpen the HTML files in {out} to see the reports. Your real data never touches demo.db.")
    return 0


def cmd_ynab_budgets(args, cfg: Config) -> int:
    from finagent.sources.ynab import YNABSource
    for b in YNABSource(cfg.ynab_token() or "").list_budgets():
        _say(f"{b['id']}  {b['name']}  (modified {b.get('last_modified_on', '?')})")
    return 0


# --------------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="finagent", description="Personal finance agent: itemize, analyze, report.")
    p.add_argument("--version", action="version", version=f"finagent {__version__}")
    p.add_argument("--config", help="path to config.toml (default: ./config.toml)")
    p.add_argument("--db", help="override the SQLite database path")
    p.add_argument("--today", help=argparse.SUPPRESS)  # testing / backfills: pretend today is YYYY-MM-DD
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    def report_opts(sp):
        sp.add_argument("--ai", action="store_true", help="add a coach's note written by Claude")
        sp.add_argument("--email", action="store_true", help="email the report (see [email] in config)")
        sp.add_argument("--open", action="store_true", help="open the HTML report in your browser")
        sp.add_argument("--format", help="comma list of html,md,json (default from config)")
        sp.add_argument("--out", help="output folder (default: reports/)")

    sub.add_parser("init", help="create config.toml and folders").add_argument("--dir")
    sub.add_parser("status", help="what's in the database")
    sub.add_parser("sync", help="pull from your configured source")
    sp = sub.add_parser("import", help="import CSV exports")
    sp.add_argument("files", nargs="+")
    sp.add_argument("--format", choices=["auto", "ynab", "monarch", "mint", "rocket", "copilot", "generic"])
    sp.add_argument("--sign", choices=["auto", "expenses_negative", "expenses_positive"])
    sp.add_argument("--account", help="account name to tag these rows with")

    sp = sub.add_parser("itemize", help="itemize every expense (terminal + CSV)")
    sp.add_argument("--months", type=int)
    sp.add_argument("--month", help="YYYY-MM")
    sp.add_argument("--from", dest="start", help="YYYY-MM-DD")
    sp.add_argument("--to", dest="end", help="YYYY-MM-DD")
    sp.add_argument("--all", action="store_true", help="print every transaction, not just category totals")
    sp.add_argument("--csv", help="where to write the CSV")
    sp.add_argument("--out")

    sp = sub.add_parser("subscriptions", help="recurring charges & subscriptions")
    sp.add_argument("--all", action="store_true", help="include giving")

    sp = sub.add_parser("review", help="3–6 month review with ranked, dollar-quantified actions")
    sp.add_argument("--months", type=int, help="how many complete months (default 6)")
    sp.add_argument("--sync", action="store_true", help="sync first")
    report_opts(sp)

    sp = sub.add_parser("weekly", help="weekly check-in (last complete week)")
    sp.add_argument("--week-of", help="any date in the week to report on (YYYY-MM-DD)")
    report_opts(sp)

    sp = sub.add_parser("monthly", help="monthly report (last complete month)")
    sp.add_argument("--month", help="YYYY-MM")
    report_opts(sp)

    sp = sub.add_parser("run", help="sync + report + email — for schedulers")
    sp.add_argument("report", choices=["weekly", "monthly", "review"])
    sp.add_argument("--week-of")
    sp.add_argument("--month")
    report_opts(sp)

    sp = sub.add_parser("demo", help="generate sample data and every report")
    report_opts(sp)

    sub.add_parser("ynab-budgets", help="list your YNAB budgets and their ids")
    return p


COMMANDS = {
    "init": cmd_init, "status": cmd_status, "sync": cmd_sync, "import": cmd_import, "itemize": cmd_itemize,
    "subscriptions": cmd_subscriptions, "review": cmd_review, "weekly": cmd_weekly, "monthly": cmd_monthly,
    "run": cmd_run, "demo": cmd_demo, "ynab-budgets": cmd_ynab_budgets,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
        return COMMANDS[args.cmd](args, cfg)
    except (CLIError, FileNotFoundError, ValueError) as err:
        _err(str(err))
        return 2
    except Exception as err:  # surface source errors (YNAB, CSV) without a traceback wall
        from finagent.sources.ynab import YNABError
        if isinstance(err, YNABError):
            _err(str(err))
            return 2
        raise
