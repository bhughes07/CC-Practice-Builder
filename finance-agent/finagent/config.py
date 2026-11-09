"""Configuration: config.toml + environment variables (secrets live only in env)."""

from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any

PACKAGE_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = PACKAGE_DIR.parent  # finance-agent/ when running from a checkout
EXAMPLE_CONFIG = PACKAGE_DIR / "config.example.toml"


def home_dir() -> Path:
    """Where config.toml, data/, imports/ and reports/ live by default."""
    if (PACKAGE_ROOT / "pyproject.toml").exists():  # source checkout / editable install
        return PACKAGE_ROOT
    return Path.cwd()

DEFAULTS: dict[str, Any] = {
    "household": {
        "name": "Our Household",
        "currency": "$",
        "week_starts_on": "monday",
    },
    "source": {
        # plaid | ynab | csv | demo — or a list, e.g. ["plaid", "csv"]
        "type": "csv",
        "budget_id": "last-used",  # YNAB: "last-used" or a budget UUID
        "history_months": 12,  # how far back `sync` pulls (12 = catch annual renewals)
    },
    "csv": {
        "paths": ["imports/*.csv"],
        "format": "auto",  # auto | ynab | monarch | mint | rocket | copilot | generic
        "sign": "auto",  # auto | expenses_negative | expenses_positive
        "date_order": "auto",  # auto | mdy | dmy
    },
    "storage": {"database": "data/finance.db"},
    "plaid": {
        # sandbox = Plaid's fake test bank; production = your real accounts (free Trial plan: 10 banks)
        "env": "production",
        "country_codes": ["US"],
        "days_requested": 730,  # history to request when linking (max 730)
        "port": 8765,  # local page used once per bank to sign in through Plaid Link
        "client_name": "finagent",
        # Keys come from PLAID_CLIENT_ID and PLAID_SECRET environment variables.
    },
    "analysis": {
        "review_months": 6,
        # Never suggested as "cuts". Matched case-insensitively against
        # "<group> <category>". Giving and saving are choices to protect.
        "protected": ["tithe", "giving", "church", "charity", "donation", "offering", "missions",
                      "saving", "invest", "retirement", "401k", "ira", "emergency fund", "529"],
        "min_monthly_impact": 5.0,  # hide suggestions worth less than this per month
        "large_transaction": 150.0,  # floor for "unusual transaction" flags
        "savings_rate_target": 0.15,
    },
    # Monthly targets used when your budgeting app doesn't supply budgets
    # (YNAB does). Keys are category names exactly as they appear in your data.
    "budgets": {},
    "categories": {
        # Merchant (normalized) → category, applied to *uncategorized* items
        # and to everything when force = true.
        "merchant_rules": {},
        "force_merchant_rules": False,
        # Category → class override: essential | discretionary | giving |
        # savings | debt | fees | other
        "class_overrides": {},
        # Categories to ignore entirely (e.g. reimbursable work expenses).
        "ignore": [],
    },
    "report": {
        "output_dir": "reports",
        "formats": ["html", "md"],
        "open_after": False,
    },
    "email": {
        "enabled": False,
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "use_starttls": True,
        "username": "",
        "from_addr": "",
        "to": [],
        "subject_prefix": "[Budget]",
        # Password is read from the FINAGENT_SMTP_PASSWORD environment variable.
    },
    "ai": {
        "enabled": False,
        "model": "claude-opus-5-5",
        "effort": "high",
        # Reads ANTHROPIC_API_KEY (or an `ant auth login` profile).
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("budgets",):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config(dict):
    """A dict of settings plus the directory relative paths resolve against."""

    def __init__(self, data: dict, base_dir: Path, path: Path | None = None):
        super().__init__(data)
        self.base_dir = base_dir
        self.path = path

    def resolve(self, p: str | os.PathLike) -> Path:
        p = Path(os.path.expanduser(str(p)))
        return p if p.is_absolute() else (self.base_dir / p)

    @property
    def currency(self) -> str:
        return self["household"]["currency"]

    @property
    def week_starts_on(self) -> int:
        names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        val = str(self["household"].get("week_starts_on", "monday")).lower()
        return names.index(val) if val in names else 0

    @property
    def sources(self) -> list[str]:
        t = self["source"]["type"]
        return [t] if isinstance(t, str) else list(t)

    @property
    def plaid_env(self) -> str:
        return os.environ.get("PLAID_ENV") or self["plaid"].get("env", "production")

    @property
    def sandbox_mode(self) -> bool:
        """Plaid's fake test bank is in use — keep its data away from the real database."""
        return "plaid" in self.sources and self.plaid_env == "sandbox"

    def database_path(self) -> Path:
        db = self["storage"]["database"]
        if self.sandbox_mode and db == DEFAULTS["storage"]["database"]:
            return self.resolve("data/sandbox.db")
        return self.resolve(db)

    # Secrets: environment only, never the config file.
    @staticmethod
    def ynab_token() -> str | None:
        return os.environ.get("YNAB_TOKEN") or os.environ.get("FINAGENT_YNAB_TOKEN")

    def plaid_keys(self) -> tuple[str | None, str | None]:
        """Client ID + the secret for the active environment (Sandbox has its own secret)."""
        secret = os.environ.get("PLAID_SECRET")
        if self.plaid_env == "sandbox" and os.environ.get("PLAID_SANDBOX_SECRET"):
            secret = os.environ["PLAID_SANDBOX_SECRET"]
        return os.environ.get("PLAID_CLIENT_ID"), secret

    @staticmethod
    def smtp_password() -> str | None:
        return os.environ.get("FINAGENT_SMTP_PASSWORD")


def find_config(explicit: str | None = None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("FINAGENT_CONFIG")
    if env:
        return Path(env).expanduser()
    for candidate in (Path.cwd() / "config.toml", home_dir() / "config.toml"):
        if candidate.exists():
            return candidate
    return None


def load_config(explicit: str | None = None, overrides: dict | None = None) -> Config:
    path = find_config(explicit)
    data: dict = {}
    if path is not None:
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        base_dir = path.resolve().parent
    else:
        base_dir = home_dir()
    merged = _deep_merge(DEFAULTS, data)
    if overrides:
        merged = _deep_merge(merged, overrides)
    return Config(merged, base_dir, path)
