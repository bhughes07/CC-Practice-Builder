# finance-agent

Personal finance agent. Python 3.11+, standard library only (optional `anthropic` for the coach's note).

## Layout
- `finagent/sources/` — data in: `ynab.py` (official API), `csv_import.py` (auto-detects YNAB, Monarch, Mint,
  Rocket Money, Copilot, generic bank CSVs), `demo.py` (synthetic household).
- `finagent/store.py` — SQLite: raw transactions + budgets only. Everything else is derived at load time.
- `finagent/categorize.py` — merchant clean-up, category → class (essential / discretionary / giving / …),
  income / expense / transfer classification.
- `finagent/ledger.py` — enriched transactions + aggregations. `insights.py` — `Analysis`: recurring
  detection, detectors, ranked `Insight`s with $ impact. `recurring.py`, `detectors.py` — the building blocks.
- `finagent/reports/` — `builders.py` (weekly / monthly / review → typed sections), `render_html.py`
  (email-safe, table-based), `render_md.py`, `model.py`.
- `finagent/cli.py` — every command. `finagent/ai.py` — optional Claude-written coach's note.

## Conventions
- Amounts are signed: negative = money out. `Transaction.outflow` is spend as a positive number.
- All math happens in Python; the AI only writes narrative from `Analysis.to_summary()` (aggregates only).
- Giving and savings are protected: never generate an insight that suggests cutting them.
- Tests: `python -m pytest -q`. Add a test for every detector / importer change.

## Never commit
`config.toml`, `data/`, `imports/`, `reports/`, `*.db`, `*.csv` (except `tests/fixtures/`). The repository is
public. Secrets (`YNAB_TOKEN`, `FINAGENT_SMTP_PASSWORD`, `ANTHROPIC_API_KEY`) live in environment variables only.
