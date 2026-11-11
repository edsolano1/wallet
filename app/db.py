"""
The SQLite database: one file on disk, created on first run.

Two things here are load bearing and come straight from the brief:

Sign normalization. Plaid reports credit and depository amounts differently.
Everything stored in `transactions.amount` is normalized on the way in, so
money going out is always negative and money coming in is always positive,
whatever kind of account it came from.

Rule 2, an account is not a category. A category is not allowed to share a
name with an account or an institution. That is enforced by triggers below,
so it cannot be broken by a bug elsewhere in the app.
"""

import sqlite3
from pathlib import Path

from .config import DATABASE_PATH

SCHEMA = """
PRAGMA foreign_keys = ON;

-- One row per bank login. Plaid calls this an "Item".
CREATE TABLE IF NOT EXISTS items (
    id                  INTEGER PRIMARY KEY,
    plaid_item_id       TEXT    NOT NULL UNIQUE,
    institution_id      TEXT,
    institution_name    TEXT,
    -- Access tokens live in the database, never in source code.
    access_token        TEXT    NOT NULL,
    -- Cursor for /transactions/sync, so each sync only asks for what changed.
    sync_cursor         TEXT,
    status              TEXT    NOT NULL DEFAULT 'healthy',
    last_sync_at        TEXT,
    last_sync_error     TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS accounts (
    id                  INTEGER PRIMARY KEY,
    plaid_account_id    TEXT    NOT NULL UNIQUE,
    item_id             INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    name                TEXT    NOT NULL,
    official_name       TEXT,
    mask                TEXT,
    -- 'depository' for checking and savings, 'credit' for credit cards.
    -- This drives the transfer logic, so it has to be right.
    type                TEXT    NOT NULL CHECK (type IN ('depository','credit','investment','loan','other')),
    subtype             TEXT,
    current_balance     REAL,
    -- The honest number: Plaid's available balance already nets out pending.
    available_balance   REAL,
    -- Closed accounts keep their history but stop syncing.
    is_closed           INTEGER NOT NULL DEFAULT 0 CHECK (is_closed IN (0,1)),
    last_synced_at      TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS categories (
    id                  INTEGER PRIMARY KEY,
    name                TEXT    NOT NULL UNIQUE,
    -- Inactive categories stay assignable. A category is never a dead end.
    is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    id                      INTEGER PRIMARY KEY,
    plaid_transaction_id    TEXT    NOT NULL UNIQUE,
    account_id              INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    -- Sign normalized: out is negative, in is positive, always.
    amount                  REAL    NOT NULL,
    date                    TEXT    NOT NULL,
    merchant_name           TEXT,
    name                    TEXT,
    pending                 INTEGER NOT NULL DEFAULT 0 CHECK (pending IN (0,1)),
    -- Set on a posted transaction, pointing back at the pending one it replaced.
    pending_transaction_id  TEXT,
    category_id             INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    -- A transfer is the absence of spending, so it is a flag, never a category.
    is_transfer             INTEGER NOT NULL DEFAULT 0 CHECK (is_transfer IN (0,1)),
    transfer_kind           TEXT    CHECK (transfer_kind IN ('card_payment','self_transfer','reimbursement')),
    -- Shared by the two legs of a matched transfer.
    transfer_pair_id        TEXT,
    created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tx_date       ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_account    ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_tx_pending    ON transactions(pending);
CREATE INDEX IF NOT EXISTS idx_tx_transfer   ON transactions(is_transfer);
CREATE INDEX IF NOT EXISTS idx_tx_pair       ON transactions(transfer_pair_id);
CREATE INDEX IF NOT EXISTS idx_tx_pending_id ON transactions(pending_transaction_id);

-- Rule 2: a category may not share a name with an account or an institution.
CREATE TRIGGER IF NOT EXISTS categories_block_clash_insert
BEFORE INSERT ON categories
FOR EACH ROW
WHEN EXISTS (SELECT 1 FROM accounts WHERE lower(name) = lower(NEW.name))
  OR EXISTS (SELECT 1 FROM items WHERE lower(institution_name) = lower(NEW.name))
BEGIN
    SELECT RAISE(ABORT, 'A category cannot share a name with an account or an institution');
END;

CREATE TRIGGER IF NOT EXISTS categories_block_clash_update
BEFORE UPDATE OF name ON categories
FOR EACH ROW
WHEN EXISTS (SELECT 1 FROM accounts WHERE lower(name) = lower(NEW.name))
  OR EXISTS (SELECT 1 FROM items WHERE lower(institution_name) = lower(NEW.name))
BEGIN
    SELECT RAISE(ABORT, 'A category cannot share a name with an account or an institution');
END;
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: Path | None = None) -> None:
    """Create the tables if they are not there yet. Safe to run every start."""
    with connect(path) as conn:
        conn.executescript(SCHEMA)


def normalize_amount(plaid_amount: float) -> float:
    """
    Turn a Plaid amount into our convention: out is negative, in is positive.

    Plaid uses one sign rule for transactions on both depository and credit
    accounts: positive when money moves out of the account, negative when it
    moves in. A card purchase is positive, a refund is negative, a payment to
    the card is negative. So a single negation is correct for every account
    type, and no per-type branching is needed here.

    Balances are the place where credit and depository really do differ, and
    that is handled where balances are read, not here.
    """
    return -plaid_amount


# Columns added after the first version shipped. SQLite cannot add a column that
# already exists, so each one is checked first. Safe to run on every start.
_LATER_COLUMNS = {
    "transactions": {
        # Plaid's own category guess. A supporting signal for transfer detection,
        # never the sole reason to call something a transfer.
        "pfc_primary": "TEXT",
        "pfc_detailed": "TEXT",
        # Rule 3: an unmatched transfer leg is surfaced, never swallowed.
        "needs_review": "INTEGER NOT NULL DEFAULT 0",
        "review_reason": "TEXT",
    }
}


ENVELOPES = """
-- Rule 4 stays intact: an envelope subtracts from SPENDABLE and never from net
-- worth. This is the "earmarking" the brief asks for, not a Monarch style budget
-- that hides money. Every dollar fenced off here is still fully counted as his.
CREATE TABLE IF NOT EXISTS envelopes (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    -- What he sets aside each month.
    amount      REAL    NOT NULL CHECK (amount >= 0),
    -- Optional: spending in this category draws the envelope down.
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    -- Optional fallback so an envelope works before anything is categorised:
    -- matches Plaid's own category, e.g. 'FOOD_AND_DRINK_GROCERIES'.
    pfc_match   TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


NOTES = """
-- What Eddie tells Claude about a transaction when the automatic rules cannot
-- know. `tag` is the part the arithmetic reads; `note` is free text that only
-- Claude reads. A row here always wins over the category rules, because he can
-- see the receipt and the rules cannot.
CREATE TABLE IF NOT EXISTS transaction_notes (
    transaction_id INTEGER PRIMARY KEY REFERENCES transactions(id) ON DELETE CASCADE,
    tag            TEXT,
    note           TEXT,
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Anything he wants to tell Claude that is not about one transaction.
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def migrate(path: Path | None = None) -> None:
    with connect(path) as conn:
        for table, columns in _LATER_COLUMNS.items():
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        conn.executescript(ENVELOPES)
        conn.executescript(NOTES)
        conn.commit()
