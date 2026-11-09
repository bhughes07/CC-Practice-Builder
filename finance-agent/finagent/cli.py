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
import time
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
    add_months, fmt_date, fmt_range, last_complete_week, money, money_auto, month_end, month_key, month_label,
    month_start, shift_month, week_start,
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
    path = Path(args.db) if getattr(args, "db", None) else cfg.database_path()
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
    problems = getattr(args, "sync_problems", None)
    if problems:  # scheduled runs: make sync trouble visible in the emailed report, not just a log
        rep.sections.insert(0, {"type": "callout", "tone": "warn", "title": "Some accounts didn't sync",
                                "body": " ".join(problems) + " Recent transactions from those accounts may be "
                                                             "missing from this report."})
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
    """Sync every configured source. `source.type` may be one name or a list (e.g. ["plaid", "csv"])."""
    sources = cfg["source"]["type"]
    sources = [sources] if isinstance(sources, str) else list(sources)
    return "\n".join(_sync_one(src, cfg, store, today) for src in sources)


def _sync_one(src: str, cfg: Config, store: Store, today: date) -> str:
    history = int(cfg["source"].get("history_months", 12))
    since = add_months(date(today.year, today.month, 1), -history)
    if src == "plaid":
        from finagent.sources.plaid import sync_all
        return sync_all(cfg, store)
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
    raise CLIError(f"Unknown source.type '{src}' (use plaid, ynab, csv, or demo)")


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
        _say(f"Source:   {', '.join(cfg.sources)}" + (" (Plaid SANDBOX — test data only)" if cfg.sandbox_mode else ""))
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
    problems: list[str] = []
    with _store(cfg, args) as store:
        try:
            out = do_sync(cfg, store, _today(args))
            _say(out)
            problems = [ln.strip() for ln in out.splitlines()
                        if any(k in ln for k in ("needs you to sign in again", "sync failed", "no banks connected",
                                                 "Set the PLAID_CLIENT_ID"))]
        except Exception as err:  # keep going with what's already in the database
            _err(f"sync failed, reporting on existing data: {err}")
            problems = [f"Sync failed ({err}); this report uses the data already on your computer."]
    args.sync_problems = problems
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


def cmd_budget_suggest(args, cfg: Config) -> int:
    """Print a ready-to-paste [budgets] block based on what you actually spend."""
    import math

    from finagent.models import CLASS_DISCRETIONARY, CLASS_FEES, CLASS_SAVINGS, CLASS_TRANSFER
    with _store(cfg, args) as store:
        L = _ledger(cfg, store, _today(args))
        A = _analysis(L, args.months or 3)
        cur = L.currency
        trim = max(0.0, min(0.5, args.trim / 100))
        window = A.window_expenses()
        rows = []
        for cat, row in A.cat_matrix_core.items():
            avg = sum(row.values()) / A.n
            cls = L.class_of(cat)
            if avg < 5 or cls in (CLASS_SAVINGS, CLASS_TRANSFER, CLASS_FEES):
                continue
            spent = [t for t in window if t.category == cat and t.id not in A.unusual_ids]
            total = sum(t.outflow for t in spent) or 1
            fixed = sum(t.outflow for t in spent if t.id in A.recurring_ids) / total >= 0.7
            if fixed:  # bills and subscriptions: budget what they cost
                target, note = math.ceil(avg / 5) * 5, "fixed"
            elif cls == CLASS_DISCRETIONARY and not L.is_protected(cat):
                goal = avg * (1 - trim)
                step = 5 if goal < 100 else 25
                target, note = max(step, round(goal / step) * step), f"-{args.trim:.0f}%"
            else:
                step = 5 if avg < 100 else 25
                target, note = max(step, round(avg / step) * step), ""
            rows.append((L.group_of(cat), cat, target, avg, note))
        one_offs = sum(t.outflow for t, _ in A.unusual) / A.n
        if one_offs >= 25:
            rows.append(("Planning", "Irregular Expenses", math.ceil(one_offs / 25) * 25, one_offs, "sinking fund"))
        rows.sort(key=lambda r: (r[0], -r[3]))

        lines = [f"# Suggested from your spending, {month_label(A.months[0])} – {month_label(A.months[-1])}.",
                 f"# Fixed bills = what they cost · lifestyle categories = {args.trim:.0f}% under your average ·",
                 "# one-off purchases moved to an 'Irregular Expenses' fund. Edit, then paste into config.toml.",
                 "[budgets]"]
        group = None
        for g, cat, target, avg, note in rows:
            if g != group:
                lines.append(f"# {g}")
                group = g
            key = cat.replace('"', '\\"')
            lines.append(f'"{key}" = {target:.0f}'.ljust(42) + f"# avg {money_auto(avg, cur)}" + (f" ({note})" if note else ""))
        total = sum(r[2] for r in rows)
        left = A.avg_income - total
        lines.append(f"# Total {money_auto(total, cur)}/mo · average income {money_auto(A.avg_income, cur)}/mo")
        if A.avg_income and left < A.avg_income * A.savings_target:
            need = A.avg_income * A.savings_target - left
            lines.append(f"# ⚠ This leaves {money_auto(left, cur)}/mo ({left / A.avg_income * 100:.0f}% of income). "
                         f"Trim about {money_auto(need, cur)} more to save {A.savings_target * 100:.0f}%.")
        elif A.avg_income:
            lines.append(f"# Leaves {money_auto(left, cur)}/mo to save ({left / A.avg_income * 100:.0f}% of income).")
        _say("\n".join(lines))
    return 0


# --------------------------------------------------------------------------- plaid

def _plaid_client(cfg: Config):
    from finagent.sources.plaid import make_client
    return make_client(cfg)


def cmd_plaid(args, cfg: Config) -> int:
    from finagent.sources.plaid import LinkSession, PlaidItems, items_path, new_item, sync_item

    items = PlaidItems(items_path(cfg))
    pc = cfg["plaid"]

    if args.plaid_cmd == "list":
        if not items.items:
            _say("No banks connected yet. Run `finagent plaid link`.")
            return 0
        for it in items.items:
            status = {"ok": "connected", "relink": "NEEDS SIGN-IN (finagent plaid link --update)"}.get(
                it.get("status", "ok"), it.get("status"))
            _say(f"{it['institution']['name']}  [{it.get('env', 'production')}] — {status}"
                 + (f" · last sync {it['last_sync'].replace('T', ' ')}" if it.get("last_sync") else " · never synced"))
            for a in it.get("accounts", {}).values():
                skip = "" if a.get("type") in ("depository", "credit", "") else "  (balances only — not counted as spending)"
                _say(f"    {a.get('name', '')} ••{a.get('mask', '')}  {a.get('subtype') or a.get('type', '')}{skip}")
        return 0

    if args.plaid_cmd == "remove":
        matches = items.find(args.name)
        if len(matches) != 1:
            raise CLIError(f"'{args.name}' matches {len(matches)} connections; use the exact bank name from "
                           f"`finagent plaid list`.")
        it = matches[0]
        try:
            _plaid_client(cfg).remove_item(it["access_token"])
        except Exception as err:  # still forget it locally
            _err(f"Plaid didn't confirm the removal ({err}); forgetting it locally anyway.")
        items.remove(it["item_id"])
        items.save()
        msg = f"Disconnected {it['institution']['name']}."
        if args.delete_data:
            with _store(cfg, args) as store:
                n = store.delete_prefix(f"plaid:{it['item_id']}:")
            msg += f" Deleted {n} of its transactions."
        _say(msg)
        return 0

    client = _plaid_client(cfg)

    if args.plaid_cmd == "sandbox":
        public_token = client.sandbox_public_token(username=args.username, days_requested=int(pc["days_requested"]))
        access_token, item_id = client.exchange_public_token(public_token)
        item = new_item(access_token, item_id, client.env,
                        {"institution": {"name": "First Platypus Bank (sandbox)", "institution_id": "ins_109508"}})
        items.upsert(item)
        items.save()
        _say(f"Connected Plaid's test bank ({args.username}).")
        with _store(cfg, args) as store:
            for attempt in range(6):
                res = sync_item(client, item, store)
                if res.error or res.added or res.status != "NOT_READY":
                    break
                time.sleep(5)  # sandbox history is usually ready within seconds
            items.save()
            _say(res.line())
            _status_line(store)
        return 0

    # link / relink
    update = None
    if args.update:
        matches = items.find(args.update)
        if len(matches) != 1:
            raise CLIError(f"'{args.update}' matches {len(matches)} connections; see `finagent plaid list`.")
        update = matches[0]
    try:
        session = LinkSession(client, items, pc["client_name"], pc["country_codes"], int(pc["days_requested"]),
                              int(args.port or pc["port"]), update)
    except OSError:
        session = LinkSession(client, items, pc["client_name"], pc["country_codes"], int(pc["days_requested"]),
                              0, update)
    _say(f"Opening {session.url}")
    _say("Sign in to each bank or credit card in the Plaid window, then click \"I'm done\". (Ctrl+C to cancel.)")
    if not args.no_browser:
        _say("If your browser didn't open, paste the address above into it.")
    try:
        linked = session.run(open_browser=not args.no_browser)
    except KeyboardInterrupt:
        linked = session.linked
    if not linked:
        _say("No banks were connected.")
        return 0
    _say("\nConnected: " + ", ".join(i["institution"]["name"] for i in linked))
    with _store(cfg, args) as store:
        for it in linked:
            _say(sync_item(client, it, store).line())
        items.save()
        _status_line(store)
    _say("Plaid keeps downloading history for a few minutes after you connect. Run `finagent sync` later to pull "
         "the rest, then `finagent budget-suggest` and `finagent review --open`.")
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

    sp = sub.add_parser("plaid", help="connect banks directly through Plaid")
    psub = sp.add_subparsers(dest="plaid_cmd", required=True, metavar="action")
    lp = psub.add_parser("link", help="connect a bank or credit card (opens a local page in your browser)")
    lp.add_argument("--update", metavar="BANK", help="re-sign-in to a bank that stopped syncing")
    lp.add_argument("--port", type=int, help="local port for the connect page (default 8765)")
    lp.add_argument("--no-browser", action="store_true", help="just print the address")
    psub.add_parser("list", help="connected banks and accounts")
    rp = psub.add_parser("remove", help="disconnect a bank")
    rp.add_argument("name", help="bank name as shown by `finagent plaid list`")
    rp.add_argument("--delete-data", action="store_true", help="also delete its transactions locally")
    tp = psub.add_parser("sandbox", help="connect Plaid's fake test bank (needs plaid.env = sandbox)")
    tp.add_argument("--username", default="user_transactions_dynamic")

    sp = sub.add_parser("budget-suggest", help="print a [budgets] block based on what you actually spend")
    sp.add_argument("--months", type=int, help="months to average (default 3)")
    sp.add_argument("--trim", type=float, default=10.0, help="percent below average for discretionary (default 10)")

    sub.add_parser("ynab-budgets", help="list your YNAB budgets and their ids")
    return p


COMMANDS = {
    "init": cmd_init, "status": cmd_status, "sync": cmd_sync, "import": cmd_import, "itemize": cmd_itemize,
    "subscriptions": cmd_subscriptions, "review": cmd_review, "weekly": cmd_weekly, "monthly": cmd_monthly,
    "run": cmd_run, "demo": cmd_demo, "ynab-budgets": cmd_ynab_budgets, "budget-suggest": cmd_budget_suggest,
    "plaid": cmd_plaid,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
        return COMMANDS[args.cmd](args, cfg)
    except (CLIError, FileNotFoundError, ValueError) as err:
        _err(str(err))
        return 2
    except Exception as err:  # surface source errors (YNAB, Plaid) without a traceback wall
        from finagent.sources.plaid import PlaidError
        from finagent.sources.ynab import YNABError
        if isinstance(err, YNABError):
            _err(str(err))
            return 2
        if isinstance(err, PlaidError):
            _err(f"Plaid: {err.display or err.message or err.code}")
            return 2
        raise
