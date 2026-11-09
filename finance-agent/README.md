# finagent: a personal finance agent

finagent reads your budgeting software, itemizes every expense, and tells you what to change. Each suggestion is
specific and comes with a dollar figure. Then it keeps you on track with weekly and monthly reports.

```
6-month budget review — May 2026 – October 2026
  Over the last 6 months you kept $208/mo — 2% of income (target 15%). Hitting the targets below
  would free up about $1,316/mo ($15,796/yr), lifting that to ~18%.

  Top actions:
   1. Dining Out is up +105%: $1,038/mo lately vs $505/mo before  ≈$533/mo
   2. Shopping is up +36%: $914/mo lately vs $672/mo before  ≈$443/mo
   3. Groceries ran over budget in 6 of 6 months  ≈$226/mo
   4. Coffee Shops ran over budget in 6 of 6 months  ≈$46/mo
   5. 5 streaming services cost $72.95/mo ($875/yr)  ≈$38/mo
```
<sub>(Output from the built-in demo household. Run `finagent demo` to see the full reports.)</sub>

## What it does

| | |
|---|---|
| **Itemizes everything** | Every expense grouped by category with subtotals and a CSV export. Each row is flagged when it's *recurring*, a *fee*, a *possible duplicate*, or *unusual*. Transfers and credit-card payments are excluded, so nothing is counted twice. |
| **3–6 month review** | Finds categories that are trending up and budgets you overspend month after month. Also covers subscriptions and price hikes, new recurring charges, bank fees, double charges, one-off costs that need a sinking fund, and budget lines you never use. Each finding comes with a specific action and a $/month estimate, ranked by impact. |
| **Weekly check-in** | What you spent this week vs. a typical week, month-to-date pace, a projected month-end total, and how much you can spend per week to land on budget. Also: bills due in the next 7 days, anything needing attention, and every transaction. |
| **Monthly report** | Income, spending, and savings rate, with budget vs. actual per category, a 6-month trend, top merchants, the subscription list, flagged items, wins, and the full itemization. |
| **Protects what matters** | Giving, tithing, and saving are never suggested as cuts. |
| **Private by design** | Runs on your computer. Data stays in a local SQLite file. Secrets are read from environment variables only. |

Reports are self-contained HTML that work in a browser and in email (Outlook, Gmail, Apple Mail), plus
Markdown and optional JSON.

## Quick start

Requires Python 3.11+. The core has **no dependencies**.

```bash
cd finance-agent
```

```bash
pip install -e .
```

```bash
finagent demo --open
```

`demo` builds a realistic sample household and writes all three reports to `reports/demo/`. It uses its own
database, so it never mixes with your real data. (Without installing, `python -m finagent demo` works too.)

## Connect your budgeting app

```bash
finagent init
```

This creates `config.toml` (git-ignored) plus the `data/`, `imports/`, and `reports/` folders.

### YNAB: live API (recommended if you use YNAB)

1. Create a Personal Access Token at **app.ynab.com → Account settings → Developer settings**.
2. Set `type = "ynab"` under `[source]` in `config.toml`.
3. Put the token in your environment. Never put it in the file.

```bash
export YNAB_TOKEN="paste-token-here"
```

```bash
finagent sync
```

Sync pulls 12 months of transactions (split transactions expanded, off-budget accounts and starting balances
ignored) **and your monthly category budgets**, so over/under-budget analysis works automatically. If you have
several budgets, `finagent ynab-budgets` lists their ids for `budget_id`.

### Monarch, Rocket Money, Copilot, Mint / Credit Karma, YNAB export, or your bank

Export your transactions to CSV and either drop the files in `imports/` and run `finagent sync`, or import them
directly:

```bash
finagent import ~/Downloads/transactions.csv
```

The format is auto-detected from the header row. Sign conventions are handled automatically (some apps export
spending as positive numbers). Re-importing the same file, or overlapping date ranges, never double-counts.

| Source | Auto-detected | Where to look |
|---|---|---|
| Monarch Money, Rocket Money, Copilot Money, Mint / Credit Karma | ✓ | The export or download (CSV) option for transactions, usually on the web app's Transactions page or in settings |
| YNAB | ✓ | The register / plan export in the web app (or use the live API above) |
| Any bank or credit card | ✓ (generic) | "Download activity" as CSV. Date + description + amount (or debit/credit columns) is enough; banner rows above the header are skipped. |

For apps without a CSV export (EveryDollar, for example), download CSVs from each bank and credit-card website.
Uncategorized rows get a best-guess category from the merchant name.

**No budgets in your app's export?** Add monthly targets in `config.toml`:

```toml
[budgets]
"Groceries" = 900
"Dining Out" = 350
```

## Commands

| Command | What it does |
|---|---|
| `finagent review --months 6` | The deep dive: ranked actions with $ impact, trends, budget adherence, subscriptions, and a full itemization. Use `--months 3` for a shorter window. |
| `finagent weekly` | Last complete week (Mon–Sun). `--week-of 2026-10-12` for another week. |
| `finagent monthly` | Last complete month. `--month 2026-09` for another. |
| `finagent itemize` | Category totals in the terminal and every transaction to CSV. `--all` prints each transaction. `--month`, `--months`, `--from/--to` set the range. |
| `finagent subscriptions` | Every recurring charge: cadence, cost per month and per year, next charge date, price changes, cancellations. |
| `finagent sync` / `import` | Pull from YNAB or (re)import CSV exports. |
| `finagent run weekly\|monthly` | `sync` + report + email. Built for schedulers. |
| `finagent status` | What's in the database. |

Report options: `--open` (open in browser), `--email`, `--ai` (coach's note), `--format html,md,json`, `--out DIR`.

## Weekly and monthly reports on autopilot

**1. Email (optional).** In `config.toml`:

```toml
[email]
enabled = true
smtp_host = "smtp.gmail.com"   # Outlook.com: smtp-mail.outlook.com · Microsoft 365: smtp.office365.com
username = "you@example.com"
to = ["you@example.com", "spouse@example.com"]
```

Then provide the password through the environment as `FINAGENT_SMTP_PASSWORD`. Use an **app password**: Gmail
and Outlook.com require one, and some Microsoft 365 organizations block SMTP sign-in entirely. If that happens,
use a personal mailbox to send.

**2. Schedule it.**

*macOS / Linux (cron):* put your secrets in `~/.config/finagent/env` (`chmod 600`), then open the crontab:

```bash
crontab -e
```

Add these lines:

```
# Weekly check-in, Mondays 7:05am
5 7 * * 1  /path/to/finance-agent/scripts/scheduled-run.sh weekly
# Monthly report, the 2nd at 7:10am (gives card transactions a day to post)
10 7 2 * *  /path/to/finance-agent/scripts/scheduled-run.sh monthly
```

*Windows (Task Scheduler):* create a weekly task with the action below, and a second one for `monthly`:

```
powershell -ExecutionPolicy Bypass -File C:\path\to\finance-agent\scripts\scheduled-run.ps1 weekly
```

Runs are logged to `data/scheduled-run.log`. If a sync fails (expired token, no network), the report still runs
on the data you already have, and the error is logged.

## Optional: a coach's note written by Claude

Add `--ai` to any report, or set `[ai] enabled = true`. A short narrative goes at the top: what's working, the
2–3 changes worth the most money, and a question to talk through together.

```bash
pip install -e ".[ai]"
```

```bash
export ANTHROPIC_API_KEY="..."
```

```bash
finagent review --ai --open
```

**What is sent:** only the aggregated summary (category totals, the detected subscriptions, and the ranked
findings). No account names, account numbers, or raw bank descriptions are sent. All the math is done locally
before anything is sent, so the note can't change the numbers. Without `--ai`, nothing leaves your machine
except the YNAB sync.

## Use it from Claude Code

Open Claude Code in `finance-agent/` and ask "how did we do last month?" or "review our last 3 months". The
bundled `budget-coach` skill runs the right commands, reads the report, and walks you through it.

## What the analysis looks for

- **Category creep:** recent months vs. the months before, ignoring one-off purchases. It's only flagged when
  sustained, habitual (2+ purchases/month), and above budget if you have one.
- **Chronic overspending:** over budget in at least half the months. When a budget has been unrealistic every
  month, it says so and offers a realistic number.
- **Subscriptions:** detected from timing and amount patterns, even several plans under one merchant (two Apple
  charges, for example). Also flags price increases, new recurring charges ("did a free trial convert?"),
  stacked streaming services, and cancellations (counted as wins).
- **Fees:** overdraft/NSF, late, interest, ATM, foreign transaction, and account maintenance, each with the
  specific fix.
- **Possible double charges:** same merchant and same amount within 3 days, excluding routine purchases like
  the same coffee order.
- **Unusual purchases:** charges well above your normal for that category or merchant. The total feeds a
  suggested "irregular expenses" sinking fund.
- **Unused budget:** money that's budgeted but never spent, which you can redirect.
- **Wins:** categories trending down, cancelled subscriptions, consistent giving, months under budget.

## Privacy and security

- Everything runs locally. The database is `data/finance.db`.
- `config.toml`, `data/`, `imports/`, `reports/`, and every `*.csv` / `*.db` are git-ignored. This repository is
  public, so keep it that way. If you want your config under version control, move this folder to a **private**
  repository.
- Tokens and passwords come only from environment variables (`YNAB_TOKEN`, `FINAGENT_SMTP_PASSWORD`,
  `ANTHROPIC_API_KEY`).
- The YNAB integration is read-only.

## Development

```bash
pip install -e ".[dev,ai]"
```

```bash
python -m pytest -q
```

The architecture overview is in `CLAUDE.md`.
