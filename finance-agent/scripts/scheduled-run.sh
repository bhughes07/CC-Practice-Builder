#!/usr/bin/env bash
# Run a scheduled report: sync → build → save → email (if [email] enabled = true).
#
#   scripts/scheduled-run.sh weekly      # e.g. Mondays 7am
#   scripts/scheduled-run.sh monthly     # e.g. the 2nd of each month
#
# Secrets are read from ~/.config/finagent/env (chmod 600), never from the repo:
#   YNAB_TOKEN=...
#   FINAGENT_SMTP_PASSWORD=...
#   ANTHROPIC_API_KEY=...        (only if [ai] enabled = true)
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE="${FINAGENT_ENV_FILE:-$HOME/.config/finagent/env}"
if [ -f "$ENV_FILE" ]; then
  set -a; . "$ENV_FILE"; set +a
fi
PYTHON="${FINAGENT_PYTHON:-python3}"
mkdir -p data
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') finagent run ${1:-weekly} ==="
  "$PYTHON" -m finagent run "${1:-weekly}"
} >> data/scheduled-run.log 2>&1
