"""A private key file so secrets live in one owner-only place — not in documents, shell history, or the repo.

Location: ~/.config/finagent/env (macOS / Linux) or %APPDATA%\\finagent\\env (Windows),
overridable with FINAGENT_ENV_FILE. Format is plain KEY=VALUE lines, the same file the
scheduled-run script reads. Real environment variables always take precedence.
"""

from __future__ import annotations

import getpass
import os
from pathlib import Path
from typing import Callable

# (name, prompt, hidden?)
KEYS: list[tuple[str, str, bool]] = [
    ("PLAID_CLIENT_ID", "Plaid client ID", False),
    ("PLAID_SECRET", "Plaid Production secret", True),
    ("PLAID_SANDBOX_SECRET", "Plaid Sandbox secret (for the fake test bank; optional)", True),
    ("YNAB_TOKEN", "YNAB personal access token (optional)", True),
    ("FINAGENT_SMTP_PASSWORD", "Email app password for sending reports (optional)", True),
    ("ANTHROPIC_API_KEY", "Anthropic API key for the coach's note (optional)", True),
]
KNOWN = {k for k, _, _ in KEYS}


def env_file_path() -> Path:
    override = os.environ.get("FINAGENT_ENV_FILE")
    if override:
        return Path(override).expanduser()
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "finagent" / "env"
    return Path.home() / ".config" / "finagent" / "env"


def read_env_file(path: Path | None = None) -> dict[str, str]:
    path = path or env_file_path()
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key.strip():
            out[key.strip()] = value
    return out


def load_env_file(path: Path | None = None) -> list[str]:
    """Put saved keys into os.environ (without overriding anything already set). Returns names loaded."""
    loaded = []
    for key, value in read_env_file(path).items():
        if value and not os.environ.get(key):
            os.environ[key] = value
            loaded.append(key)
    return loaded


def write_env_file(values: dict[str, str], path: Path | None = None) -> Path:
    path = path or env_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    lines = ["# finagent keys — private. Do not share, sync, or commit this file."]
    for key, value in values.items():
        if value:
            if any(c in value for c in "\n\r"):
                raise ValueError(f"{key} contains a line break — paste just the key itself.")
            lines.append(f"{key}={value}")
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:  # Windows: the file lives in your per-user AppData folder
        pass
    return path


def prompt_and_save(ask: Callable[[str], str] = input, ask_hidden: Callable[[str], str] = getpass.getpass,
                    say: Callable[[str], None] = print, only: list[str] | None = None) -> Path:
    path = env_file_path()
    current = read_env_file(path)
    say(f"Saving keys to {path} (readable only by your user account).")
    say("Paste each value and press Enter. Secrets are hidden as you type. Press Enter alone to keep the "
        "saved value or skip.\n")
    for key, label, hidden in KEYS:
        if only and key not in only:
            continue
        status = " [saved]" if current.get(key) else ""
        value = (ask_hidden if hidden else ask)(f"{label}{status}: ").strip()
        if value:
            current[key] = value
    write_env_file(current, path)
    saved = [k for k, _, _ in KEYS if current.get(k)]
    say(f"\nSaved: {', '.join(saved) if saved else 'nothing'}.")
    return path
