"""Local SQLite store. Raw transactions + budgets; everything else is derived."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from finagent.models import Budget, Transaction

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id             TEXT PRIMARY KEY,
    date           TEXT NOT NULL,
    amount         REAL NOT NULL,
    payee          TEXT NOT NULL DEFAULT '',
    category       TEXT NOT NULL DEFAULT '',
    category_group TEXT NOT NULL DEFAULT '',
    account        TEXT NOT NULL DEFAULT '',
    memo           TEXT NOT NULL DEFAULT '',
    source         TEXT NOT NULL DEFAULT '',
    is_transfer    INTEGER NOT NULL DEFAULT 0,
    pending        INTEGER NOT NULL DEFAULT 0,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE TABLE IF NOT EXISTS budgets (
    month          TEXT NOT NULL,
    category       TEXT NOT NULL,
    category_group TEXT NOT NULL DEFAULT '',
    amount         REAL NOT NULL,
    PRIMARY KEY (month, category)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------ transactions
    def upsert_transactions(self, txns: Iterable[Transaction]) -> tuple[int, int]:
        """Insert or update. Returns (inserted, updated)."""
        now = datetime.now().isoformat(timespec="seconds")
        inserted = updated = 0
        with self.conn:
            for t in txns:
                exists = self.conn.execute("SELECT 1 FROM transactions WHERE id = ?", (t.id,)).fetchone()
                self.conn.execute(
                    """INSERT INTO transactions
                       (id, date, amount, payee, category, category_group, account, memo, source,
                        is_transfer, pending, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET
                         date=excluded.date, amount=excluded.amount, payee=excluded.payee,
                         category=excluded.category, category_group=excluded.category_group,
                         account=excluded.account, memo=excluded.memo, source=excluded.source,
                         is_transfer=excluded.is_transfer, pending=excluded.pending,
                         updated_at=excluded.updated_at""",
                    (t.id, t.date.isoformat(), round(t.amount, 2), t.payee, t.category, t.category_group,
                     t.account, t.memo, t.source, int(t.is_transfer), int(t.pending), now),
                )
                if exists:
                    updated += 1
                else:
                    inserted += 1
        return inserted, updated

    def replace_window(self, source: str, start: date, txns: list[Transaction]) -> tuple[int, int, int]:
        """Make `source`'s rows on/after `start` exactly match `txns` (handles deletions)."""
        keep = {t.id for t in txns}
        existing = {
            r["id"] for r in self.conn.execute(
                "SELECT id FROM transactions WHERE source = ? AND date >= ?", (source, start.isoformat())
            )
        }
        stale = existing - keep
        with self.conn:
            self.conn.executemany("DELETE FROM transactions WHERE id = ?", [(i,) for i in stale])
        inserted, updated = self.upsert_transactions(txns)
        return inserted, updated, len(stale)

    def transactions(self, start: date | None = None, end: date | None = None) -> list[Transaction]:
        sql, args = "SELECT * FROM transactions WHERE 1=1", []
        if start:
            sql += " AND date >= ?"
            args.append(start.isoformat())
        if end:
            sql += " AND date <= ?"
            args.append(end.isoformat())
        sql += " ORDER BY date, id"
        return [
            Transaction(
                id=r["id"], date=date.fromisoformat(r["date"]), amount=r["amount"], payee=r["payee"],
                category=r["category"], category_group=r["category_group"], account=r["account"],
                memo=r["memo"], source=r["source"], is_transfer=bool(r["is_transfer"]),
                pending=bool(r["pending"]),
            )
            for r in self.conn.execute(sql, args)
        ]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    def date_bounds(self) -> tuple[date, date] | None:
        row = self.conn.execute("SELECT MIN(date), MAX(date) FROM transactions").fetchone()
        if not row or row[0] is None:
            return None
        return date.fromisoformat(row[0]), date.fromisoformat(row[1])

    def clear(self, source: str | None = None) -> int:
        with self.conn:
            if source:
                cur = self.conn.execute("DELETE FROM transactions WHERE source = ?", (source,))
                self.conn.execute("DELETE FROM meta WHERE key LIKE ?", (f"{source}:%",))
            else:
                cur = self.conn.execute("DELETE FROM transactions")
                self.conn.execute("DELETE FROM budgets")
        return cur.rowcount

    # ------------------------------------------------------------ budgets
    def upsert_budgets(self, budgets: Iterable[Budget]) -> int:
        n = 0
        with self.conn:
            for b in budgets:
                self.conn.execute(
                    """INSERT INTO budgets (month, category, category_group, amount) VALUES (?,?,?,?)
                       ON CONFLICT(month, category) DO UPDATE SET
                         amount=excluded.amount, category_group=excluded.category_group""",
                    (b.month, b.category, b.category_group, round(b.amount, 2)),
                )
                n += 1
        return n

    def budgets(self) -> list[Budget]:
        return [
            Budget(month=r["month"], category=r["category"], amount=r["amount"], category_group=r["category_group"])
            for r in self.conn.execute("SELECT * FROM budgets ORDER BY month, category")
        ]

    # ------------------------------------------------------------ meta
    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
