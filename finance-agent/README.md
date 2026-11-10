# finagent: a personal finance agent

finagent connects straight to your bank and credit-card accounts through Plaid. It can also read your budgeting
software or CSV exports. It itemizes every expense and tells you what to change, with a specific dollar figure for
each suggestion. Then it keeps you on track with weekly and monthly reports.

```
6-month budget review — May 2026 – October 2026
  Over the last 6 months you kept $208/mo — 2% of income (target 15%). Hitting the targets below
  would free up about $1,319/mo ($15,826/yr), lifting that to ~18%.

  Top actions:
   1. Dining Out is up 105%: $1,038/mo lately vs $505/mo before  ≈$533/mo
   2. Shopping is up 36%: $914/mo lately vs $672/mo before  ≈$443/mo
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

## Connect your bank

```bash
finagent init
```

This creates `config.toml` (git-ignored) plus the `data/`, `imports/`, and `reports/` folders. It's set up for
Plaid by default (`[source] type = "plaid"`).

### Plaid: connect directly to your banks (recommended)

Plaid is the service many budgeting apps use to read bank transactions. You get your own free Plaid account, so
your data goes from Plaid straight to your computer. No other app sits in the middle.

**1. Create a Plaid account** at [dashboard.plaid.com](https://dashboard.plaid.com/signup). New accounts start on
Plaid's free **Trial** plan: real bank data, no charge, up to 10 bank logins. One login covers every account you
have at that bank. Two notes:

- Accounts created **before April 15, 2026** are on the older "Limited Production" tier, which can't connect to
  Chase, Bank of America, or Wells Fargo. Create a new account if that's you.
- For banks with their own sign-in page (Chase, Bank of America, Wells Fargo, Capital One…), the Dashboard may ask
  you to finish your profile first. It also shows which banks are enabled for your account.

**2. Save your keys.** On the Dashboard's *Keys* page, copy the **client ID**, the **Production secret**, and the
**Sandbox secret** (for the fake test bank) into finagent:

```bash
finagent keys --plaid
```

It asks for each key with hidden input, so nothing shows on screen or lands in your shell history. The keys are
saved to a private file that only your user account can read (`~/.config/finagent/env`, or
`%APPDATA%\finagent\env` on Windows), and every finagent command loads it automatically. `finagent keys --show`
lists what's saved without printing any values.

Keep the keys in that file and a password manager, not in a document, note, or email. If you copied them
somewhere else, delete that copy. If that place was ever shared or synced to a work account, generate a new
secret on Plaid's *Keys* page and save it again with `finagent keys --plaid`.

**3. Optional: try Plaid's fake test bank first.** This uses your Sandbox secret and a separate test database,
so it never mixes with your real data:

```bash
PLAID_ENV=sandbox finagent plaid sandbox
```

```bash
PLAID_ENV=sandbox finagent review --open
```

**4. Connect your real banks:**

```bash
finagent plaid link
```

Your browser opens a page served from your own computer (`http://127.0.0.1:8765/…`). Click **Connect a bank**,
pick your bank, and sign in through Plaid's secure window. Your bank password goes to Plaid, never to finagent.
Repeat for each bank and credit card, then click **I'm done**.

**5. Pull your history and set budgets.** Plaid keeps downloading up to 24 months of history for a few minutes
after you connect, so wait a few minutes, then run:

```bash
finagent sync
```

```bash
finagent budget-suggest
```

`budget-suggest` prints a `[budgets]` block based on what you actually spend. Fixed bills are set at what they
cost, lifestyle categories 10% under your average, and one-off purchases go into an "Irregular Expenses" fund. It
also warns if the total doesn't leave room to save. Edit the block and paste it into `config.toml`. Then run:

```bash
finagent review --months 6 --open
```

**Day to day:**
- `finagent sync` downloads only what changed: new transactions, pending charges that posted, and corrections.
  The scheduled `finagent run weekly|monthly` does this for you.
- `finagent plaid list` shows connected banks and accounts.
- If a bank asks you to sign in again (banks expire access periodically, often yearly), sync tells you to run
  `finagent plaid link --update "Bank Name"`.
- `finagent plaid remove "Bank Name"` disconnects a bank. Add `--delete-data` to delete its transactions too.

**What gets counted:** checking, savings, and credit cards. Loan and investment accounts are listed but their
balances aren't counted as spending. Transfers between your own accounts and credit-card payments are excluded.
Venmo / Zelle / Cash App payments *to* people count as spending ("Payments to People"), and ATM withdrawals as
"Cash & ATM". Plaid's categories are mapped to everyday names (Groceries, Dining Out, Gas & Fuel, Giving…). Use
`[categories.merchant_rules]` with `force_merchant_rules = true` to override any of them.

### YNAB: live API (if you use YNAB)

1. Create a Personal Access Token at **app.ynab.com → Account settings → Developer settings**.
2. Set `type = "ynab"` under `[source]` in `config.toml`.
3. Save the token with `finagent keys` (the "YNAB personal access token" prompt). Never put it in `config.toml`.

```bash
finagent keys
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

**No budgets in your app's export?** Run `finagent budget-suggest`, or add monthly targets in `config.toml`:

```toml
[budgets]
"Groceries" = 900
"Dining Out" = 350
```

## Your budget spreadsheet

Prefer working in a spreadsheet? `finagent sheet` creates **`budget.xlsx`** and opens it in Excel. It needs one
free add-on, installed once:

```bash
python3 -m pip install openpyxl
```

```bash
python3 -m finagent sheet
```

- **Budget tab:** one row per budget line, in your order. Type the monthly amount in the yellow column, next to
  what you actually spent over the last 3 months, the last 6 months, and last month. Totals at the top update as
  you type: regular income, total budget, what's left over, your savings rate vs. your target (also editable),
  and how much spending isn't in your plan yet.
- **Matches:** use your own line names (like the ones from EveryDollar). In each line's yellow *Matches* cell, list
  the words that identify its transactions, separated by commas. These can be merchant names (`AT&T`,
  `Disney, Hulu`) or a bank category (`Groceries`). finagent files every matching transaction under that line
  automatically. Add an amount to separate charges from the same merchant: `Apple $2.99` for iCloud and `Apple`
  for everything else from Apple.
- **Not in your plan yet:** spending that matched none of your lines, with averages. Type a budget next to one to
  make it a line, or add its name to an existing line's Matches.
- **Income tab:** every income source with a **Regular income?** Yes/No dropdown. Mark your paychecks *Yes* and
  one-time money (tax refunds, settlements, gifts) *No*. Only *Yes* income counts toward your budget.
- **Transactions tab:** every expense from the last 6 months, newest first, with the budget line it landed on and
  **Why it's here**: the Matches word that caught it (`Matches "Apple"`), its bank category, or your own choice.
  Use the filter arrows to review one line. If a line's average looks wrong, this tab shows which transactions
  are behind it. To file one transaction somewhere else, pick a line in the yellow **Move to line** dropdown. Pick
  **Exclude** for anything that isn't household spending. Excluded rows stay listed in grey, and clearing the cell
  undoes it.

Already have your budget lines in another workbook (for example, a starter built from your old budgeting app)?
`finagent sheet --import ~/Downloads/starter.xlsx` uses its lines and keeps your Income tab choices.

Save it as an Excel file (.xlsx). Every finagent report reads it automatically, so the spreadsheet is your
budget. `budget-set` and `budget-suggest --save` also write into it. Run `finagent sheet` again any time, with
the file closed, to refresh the averages. Your budgets, notes, Yes/No choices, and added rows are kept, and the
previous copy is saved as `budget.backup.xlsx`.

- **Numbers (Mac):** use *File → Export To → Excel* and replace `budget.xlsx`. Numbers saves in its own format
  otherwise.
- **Privacy:** the file is git-ignored, so it stays on your computer.

### Giving goal

To aim for a share of income, like a 10% tithe, run `finagent sheet --giving-goal 10` (or type it in the
sheet's yellow *Giving goal* cell). Reports then show giving against the goal. When you're short, the first
recommendation is a funding plan: which specific changes free up the money, and whether that covers it next
month or should be stepped up. Giving is never suggested as a cut.

### Credit-card payments

finagent decides for you, so nothing is counted twice:
- **Card not connected:** money that leaves checking to pay it counts as spending, on a line called
  **Credit Card Payments**.
- **Card connected:** finagent sees the payment arrive on the card and treats it as a transfer, because the
  card's purchases are already counted one by one.

To count every card payment regardless, set `card_payments = "spending"` under `[categories]` in `config.toml`.
A card payment is only ever filed under a debt line (such as *Credit Card Payments* or *Apple Card*), never under
a merchant line that happens to share a word with the card.

A card used only for business or influencer purchases, paid back so it never touches the household budget, can be
left out entirely: connect it and add its name under `ignore_accounts` in `[categories]`, for example
`ignore_accounts = ["Amazon Card"]`. To skip a single purchase instead, use **Exclude** on the Transactions tab.

### Regular vs. one-time income

Budgets and savings rates use **regular income only**: paychecks, and deposits that arrive on a schedule. Tax
refunds (IRS), settlements, gifts, and other one-off deposits are listed in reports as *"Not counted as regular
income"*, with a nudge to give that money a job. The spreadsheet's Income tab has the final say. You can also
set rules in `config.toml` (`[income] regular = [...]` / `one_time = [...]`).

## Commands

| Command | What it does |
|---|---|
| `finagent review --months 6` | The deep dive: ranked actions with $ impact, trends, budget adherence, subscriptions, and a full itemization. Use `--months 3` for a shorter window. |
| `finagent weekly` | Last complete week (Mon–Sun). `--week-of 2026-10-12` for another week. |
| `finagent monthly` | Last complete month. `--month 2026-09` for another. |
| `finagent itemize` | Category totals in the terminal and every transaction to CSV. `--all` prints each transaction. `--month`, `--months`, `--from/--to` set the range. |
| `finagent subscriptions` | Every recurring charge: cadence, cost per month and per year, next charge date, price changes, cancellations. |
| `finagent sync` / `import` | Pull from Plaid or YNAB, or (re)import CSV exports. |
| `finagent plaid link` / `list` / `remove` | Connect, list, or disconnect banks. `link --update "Bank"` re-signs-in. |
| `finagent budget-suggest` | Suggested budgets from your real spending. `--save` writes them for you. |
| `finagent sheet` | Create or refresh `budget.xlsx`, where you edit budgets and income choices in Excel. |
| `finagent budgets` | Your budgets next to what you actually spend, and what's left to save. |
| `finagent budget-set "Dining Out=900"` | Change one or more budgets. `=0` removes one. |
| `finagent transactions "Apple"` | Every expense on a budget line, or matching a merchant or any text, with why each landed there. Shows 3- and 6-month averages per line and the biggest merchants. `--months`, `--limit`. |
| `finagent budget-match "Gas & Fuel" "Convenience Stores"` | Add words to a line's Matches. `--replace` swaps them all, and `--remove` takes one out. |
| `finagent keys` | Save API keys to a private file with hidden input. `--plaid` asks for Plaid keys only; `--show` lists what's saved. |
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

Then save the password with `finagent keys` (it's the "email app password" prompt). Use an **app password**: Gmail
and Outlook.com require one, and some Microsoft 365 organizations block SMTP sign-in entirely. If that happens,
use a personal mailbox to send.

**2. Schedule it.**

*macOS / Linux (cron):* save your keys with `finagent keys` first (scheduled runs read the same private file),
then open the crontab:

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
finagent keys
```

Enter your Anthropic API key at the last prompt.

```bash
finagent review --ai --open
```

**What is sent:** only the aggregated summary (category totals, the detected subscriptions, and the ranked
findings). No account names, account numbers, or raw bank descriptions are sent. All the math is done locally
before anything is sent, so the note can't change the numbers. Without `--ai`, nothing leaves your machine
except the sync with your data source (Plaid or YNAB).

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
- **Plaid:** bank access tokens are kept in `data/plaid.json`, which only your user account can read. Together
  with your Plaid keys, they allow *read-only* access to transactions. They can't move money. Anyone with access to
  your computer account could read them, so keep the computer locked. `finagent plaid remove` revokes a bank's
  token at Plaid. You can also revoke access from your bank's website or at
  [my.plaid.com](https://my.plaid.com).
- The bank-connect page only listens on `127.0.0.1` (your own computer). It uses a random one-time address, rejects
  requests from other sites, and shuts down when you click **I'm done**.
- `config.toml`, `data/`, `imports/`, `reports/`, and every `*.csv` / `*.db` are git-ignored. This repository is
  public, so keep it that way. If you want your config under version control, move this folder to a **private**
  repository.
- Keys and passwords live only in your private key file (`finagent keys`) or your environment variables, never in
  `config.toml` or the repository. Environment variables take precedence over the file.
- The YNAB integration is read-only.

## Development

```bash
pip install -e ".[dev,ai]"
```

```bash
python -m pytest -q
```

The architecture overview is in `CLAUDE.md`.
