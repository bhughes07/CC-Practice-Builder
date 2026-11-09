# Run a scheduled report on Windows (Task Scheduler): sync -> build -> save -> email.
#
#   powershell -ExecutionPolicy Bypass -File scripts\scheduled-run.ps1 weekly
#   powershell -ExecutionPolicy Bypass -File scripts\scheduled-run.ps1 monthly
#
# Save keys once with `finagent keys` (stored in %APPDATA%\finagent\env, readable only by you);
# every finagent command, including scheduled runs, loads them automatically.
param([ValidateSet("weekly", "monthly", "review")][string]$Report = "weekly")

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
New-Item -ItemType Directory -Force -Path data | Out-Null
$python = if ($env:FINAGENT_PYTHON) { $env:FINAGENT_PYTHON } else { "py" }
"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') finagent run $Report ===" | Add-Content data\scheduled-run.log
& $python -m finagent run $Report *>> data\scheduled-run.log
