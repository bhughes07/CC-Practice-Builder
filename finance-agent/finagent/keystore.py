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
            out[key.strip()] = clean(key.strip(), value) if key.strip() in PLAID_FORMATS else value
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


# Plaid keys are hexadecimal: client IDs are 24 characters, secrets 30.
PLAID_FORMATS = {"PLAID_CLIENT_ID": ("client ID", 24), "PLAID_SECRET": ("secret", 30),
                 "PLAID_SANDBOX_SECRET": ("secret", 30)}
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)
_QUOTES = "\"'\u201c\u201d\u2018\u2019`"


def clean(key: str, value: str) -> str:
    """Undo what copying out of documents does: invisible characters, smart quotes, stray spaces."""
    v = value.translate(_INVISIBLE).replace("\u00a0", " ").strip().strip(_QUOTES).strip()
    if key in PLAID_FORMATS:
        v = "".join(v.split())  # keys never contain spaces
        n = PLAID_FORMATS[key][1]
        # Pasted twice into a hidden prompt (nothing shows, so people paste again): keep one copy.
        if len(v) == 2 * n and v[:n] == v[n:] and _is_hex(v):
            v = v[:n]
    return v


def _is_hex(v: str) -> bool:
    return bool(v) and all(c in "0123456789abcdefABCDEF" for c in v)


def diagnose(key: str, value: str | None) -> list[str]:
    """Plain-English problems with a key's format. Never includes the value itself."""
    if key not in PLAID_FORMATS:
        return [] if value else ["is empty"]
    name, length = PLAID_FORMATS[key]
    if not value:
        return ["is empty"]
    problems = []
    if any(c in value for c in "•●*·"):
        problems.append("contains dots or asterisks — that's the hidden (masked) version. Use the copy button "
                        "next to it on Plaid's Keys page")
    elif any(ord(c) > 127 for c in value):
        problems.append("contains unusual characters (often picked up when copying from Word or a web page)")
    elif any(c.isspace() for c in value):
        problems.append("contains spaces")
    elif not _is_hex(value):
        problems.append(f"has characters a Plaid {name} never contains (they use only 0–9 and a–f) — it may be "
                        f"a label, a placeholder, or a different key")
    if not problems and len(value) == 2 * length and value[:length] == value[length:]:
        problems.append("was pasted twice (it's the same key repeated)")
    elif len(value) != length and not problems:
        problems.append(f"is {len(value)} characters long; Plaid {name}s are {length}")
    return problems


def check(say: Callable[[str], None] = print) -> bool:
    """Report where each Plaid key comes from and whether it looks right. Returns True if all good."""
    path = env_file_path()
    saved = read_env_file(path)
    ok = True
    say(f"Key file: {path}")
    for key in PLAID_FORMATS:
        env_value = os.environ.get(key)
        file_value = saved.get(key)
        # load_env_file() copies the file into the environment, so only a *different* value means an override.
        overridden = env_value and file_value and env_value != file_value
        value, source = ((env_value, "your Terminal environment (overrides the saved file)") if overridden
                         else (file_value, "saved file") if file_value
                         else (env_value, "your Terminal environment") if env_value else (None, ""))
        problems = diagnose(key, value)
        if key == "PLAID_SANDBOX_SECRET" and not value:
            say(f"  {key:22} not saved (only needed for the fake test bank)")
            continue
        if problems:
            ok = False
            say(f"  {key:22} PROBLEM — from {source or 'nowhere'}: it " + "; ".join(problems) + ".")
        else:
            say(f"  {key:22} looks right ({len(value)} characters, from {source}).")
        if overridden:
            ok = False
            say(f"  {'':22} Note: an `export {key}=...` in your shell is overriding the saved key. Remove that "
                f"line from ~/.zshrc (or run `unset {key}`) so the saved key is used.")
    sec, sbx = saved.get("PLAID_SECRET"), saved.get("PLAID_SANDBOX_SECRET")
    if sec and sbx and sec == sbx:
        ok = False
        say("  The Production and Sandbox secrets are identical — one of them was pasted into the wrong prompt.")
    return ok


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
        for attempt in range(3):
            value = clean(key, (ask_hidden if hidden else ask)(f"{label}{status}: "))
            if not value:
                break
            problems = diagnose(key, value)
            if not problems:
                current[key] = value
                break
            say(f"  That {label.split(' (')[0]} " + "; ".join(problems) + ".")
            if attempt < 2:
                say("  Try pasting it again (or press Enter to skip):")
            else:
                say("  Skipped — nothing saved for this one.")
    write_env_file(current, path)
    saved = [k for k, _, _ in KEYS if current.get(k)]
    say(f"\nSaved: {', '.join(saved) if saved else 'nothing'}.")
    return path
