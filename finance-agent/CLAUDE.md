# finance-agent

Personal finance agent. Python 3.11+, standard library only (optional `anthropic` for the coach's note).

## Layout
- `finagent/sources/` — data in: `plaid.py` (bank connections: Link page on 127.0.0.1, `/transactions/sync`
  with cursors, Plaid categories → finagent categories), `ynab.py` (official API), `csv_import.py`
  (auto-detects YNAB, Monarch, Mint, Rocket Money, Copilot, generic bank CSVs), `demo.py` (synthetic household).
- `finagent/store.py` — SQLite: raw transactions + budgets only. Everything else is derived at load time.
- `finagent/categorize.py` — merchant clean-up, category → class (essential / discretionary / giving / …),
  income / expense / transfer classification.
- `finagent/ledger.py` — enriched transactions + aggregations. `insights.py` — `Analysis`: recurring
  detection, detectors, ranked `Insight`s with $ impact. `recurring.py`, `detectors.py` — the building blocks.
- `finagent/reports/` — `builders.py` (weekly / monthly / review → typed sections), `render_html.py`
  (email-safe, table-based), `render_md.py`, `model.py`.
- `finagent/budgeting.py` — suggested budgets from real spending. `finagent/sheet.py` — budget.xlsx (Budget,
  Income and Transactions tabs) written with openpyxl and read back on every run; when it exists it's the source
  of budgets, of which income sources count as regular, and of per-transaction "Move to line" / "Exclude" choices
  (keyed by transaction ID, applied in `categorize.enrich`). `Transaction.matched_by` / `why_here()` explain
  which Matches word filed a transaction; card payments only ever match debt lines.
- Income: `Ledger.incomes` is *regular* income only (paychecks / scheduled deposits, or the spreadsheet's
  "Yes" sources); refunds, settlements and other one-time money live in `Ledger.one_time_incomes`.
- `finagent/cli.py` — every command. `finagent/ai.py` — optional Claude-written coach's note.

## Conventions
- Amounts are signed: negative = money out. `Transaction.outflow` is spend as a positive number.
- All math happens in Python; the AI only writes narrative from `Analysis.to_summary()` (aggregates only).
- Giving and savings are protected: never generate an insight that suggests cutting them.
- Tests: `python -m pytest -q`. Add a test for every detector / importer change.

## Never commit
`config.toml`, `budget.xlsx`, `data/` (includes `plaid.json` with bank access tokens), `imports/`, `reports/`,
`*.db`, `*.csv`
(except `tests/fixtures/`). The repository is public. Secrets (`PLAID_CLIENT_ID`, `PLAID_SECRET`, `YNAB_TOKEN`,
`FINAGENT_SMTP_PASSWORD`, `ANTHROPIC_API_KEY`) live in environment variables only.
