"""
The local web server. Binds to 127.0.0.1 only, so nothing on your network
or the internet can reach it.
"""

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, envelopes, plaid_client, sync, transfers, wallet

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Personal Finance App", docs_url=None, redoc_url=None)

# Plaid account types we understand. Anything else is stored as 'other'.
KNOWN_TYPES = {"depository", "credit", "investment", "loan"}


@app.on_event("startup")
def startup() -> None:
    db.init_db()
    db.migrate()


@app.get("/api/status")
def status() -> dict:
    with db.connect() as conn:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in ("items", "accounts", "transactions", "categories")
        }
        accounts = [
            dict(r)
            for r in conn.execute(
                "SELECT name, mask, type, subtype, current_balance, available_balance"
                " FROM accounts ORDER BY type, name"
            )
        ]
        banks = [
            dict(r)
            for r in conn.execute(
                "SELECT id, institution_name, status, last_sync_at, last_sync_error"
                " FROM items ORDER BY id"
            )
        ]
        # Under-reporting looks like this: the bank gives a balance but sends no
        # transactions to explain it. A card sitting at zero with no transactions
        # is simply unused, so it must NOT be flagged or the warning becomes noise
        # that gets ignored. Checked against Chase on 2026-11-07: the Slate card
        # really was a zero balance unused card, not a hidden one.
        silent = [
            dict(r)
            for r in conn.execute(
                "SELECT a.name, a.mask, a.type, a.current_balance, i.institution_name"
                " FROM accounts a JOIN items i ON i.id = a.item_id"
                " WHERE a.is_closed = 0"
                "   AND COALESCE(a.current_balance, 0) <> 0"
                "   AND (SELECT COUNT(*) FROM transactions t WHERE t.account_id = a.id) = 0"
                "   AND EXISTS (SELECT 1 FROM accounts s JOIN transactions t2 ON t2.account_id = s.id"
                "               WHERE s.item_id = a.item_id)"
            )
        ]

        review_count = conn.execute(
            "SELECT COUNT(*) AS n FROM transactions WHERE needs_review = 1"
        ).fetchone()["n"]
        # Rule 1: transfers are the absence of spending, so they are excluded here.
        spent = conn.execute(
            "SELECT COALESCE(SUM(-amount), 0) AS s FROM transactions"
            " WHERE is_transfer = 0 AND amount < 0"
            "   AND date >= date('now','start of month')"
        ).fetchone()["s"]
        transfers_excluded = conn.execute(
            "SELECT COUNT(*) AS n FROM transactions WHERE is_transfer = 1"
        ).fetchone()["n"]

        env = envelopes.summary(conn)
        pay = wallet.summary(conn)

    # Rule 4: spendable is checking only. The wallet is spendable with the
    # commitments he has already fenced off taken out, shown as three visible
    # lines rather than one merged number.
    spendable = sum(
        (a["available_balance"] if a["available_balance"] is not None else (a["current_balance"] or 0))
        for a in accounts
        if a["type"] == "depository" and a["subtype"] == "checking"
    )
    return {
        "spendable": round(spendable, 2),
        "pay": pay,
        "envelopes": env,
        "database_file": db.DATABASE_PATH.name,
        "database_ready": True,
        "plaid_configured": config.plaid_is_configured(),
        "plaid_env": config.PLAID_ENV,
        "counts": counts,
        "banks": banks,
        "accounts": accounts,
        "review_count": review_count,
        "silent_accounts": silent,
        "spent_this_month": spent,
        "transfers_excluded": transfers_excluded,
    }


@app.post("/api/link/token")
def link_token() -> dict:
    if not config.plaid_is_configured():
        raise HTTPException(400, "Plaid keys are missing from .env")
    try:
        return {"link_token": plaid_client.create_link_token()}
    except Exception as e:
        raise HTTPException(502, f"Plaid refused to start a link session: {e}")


class ExchangeBody(BaseModel):
    public_token: str


@app.post("/api/link/exchange")
def link_exchange(body: ExchangeBody) -> dict:
    try:
        access_token, item_id = plaid_client.exchange_public_token(body.public_token)
    except Exception as e:
        raise HTTPException(502, f"Could not finish linking: {e}")

    institution = plaid_client.get_institution_name(access_token)
    accounts = plaid_client.get_accounts(access_token)

    with db.connect() as conn:
        row_id = conn.execute(
            "INSERT INTO items (plaid_item_id, institution_name, access_token)"
            " VALUES (?, ?, ?)"
            " ON CONFLICT(plaid_item_id) DO UPDATE SET access_token = excluded.access_token"
            " RETURNING id",
            (item_id, institution, access_token),
        ).fetchone()["id"]

        for a in accounts:
            balances = a.get("balances") or {}
            acct_type = str(a.get("type") or "other")
            if acct_type not in KNOWN_TYPES:
                acct_type = "other"
            conn.execute(
                "INSERT INTO accounts (plaid_account_id, item_id, name, official_name,"
                " mask, type, subtype, current_balance, available_balance, last_synced_at)"
                " VALUES (?,?,?,?,?,?,?,?,?, datetime('now'))"
                " ON CONFLICT(plaid_account_id) DO UPDATE SET"
                "   current_balance = excluded.current_balance,"
                "   available_balance = excluded.available_balance,"
                "   last_synced_at = excluded.last_synced_at",
                (
                    a.get("account_id"),
                    row_id,
                    a.get("name") or "Account",
                    a.get("official_name"),
                    a.get("mask"),
                    acct_type,
                    str(a.get("subtype")) if a.get("subtype") else None,
                    balances.get("current"),
                    balances.get("available"),
                ),
            )
        conn.commit()

    return {"ok": True, "institution": institution, "accounts_added": len(accounts)}


@app.post("/api/sync")
def run_sync() -> dict:
    """Pull transactions for every connected bank, then re-run transfer matching."""
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
    if not n:
        raise HTTPException(400, "No bank connected yet")
    out = sync.sync_all()
    out["transfers"] = transfers.run()
    return out


@app.get("/api/transactions")
def transactions(limit: int = 50) -> dict:
    with db.connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT t.id, t.date, t.amount, t.pending, t.is_transfer, t.transfer_kind,"
                " t.needs_review, t.category_id, COALESCE(t.merchant_name, t.name) AS what,"
                " a.name AS account, n.tag, n.note"
                " FROM transactions t JOIN accounts a ON a.id = t.account_id"
                " LEFT JOIN transaction_notes n ON n.transaction_id = t.id"
                " ORDER BY t.date DESC, t.id DESC LIMIT ?",
                (limit,),
            )
        ]
        pending_total = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM transactions"
            " WHERE pending = 1 AND is_transfer = 0"
        ).fetchone()["s"]
    return {"transactions": rows, "pending_total": pending_total}


@app.get("/api/review")
def review() -> dict:
    """Rule 3: unmatched legs are surfaced here rather than guessed at."""
    with db.connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT t.id, t.date, t.amount, t.review_reason,"
                " COALESCE(t.merchant_name, t.name) AS what, a.name AS account"
                " FROM transactions t JOIN accounts a ON a.id = t.account_id"
                " WHERE t.needs_review = 1 ORDER BY t.date DESC"
            )
        ]
    return {"items": rows}


class ResolveBody(BaseModel):
    transaction_id: int
    resolution: str
    pair_with: int | None = None


@app.post("/api/review/resolve")
def review_resolve(body: ResolveBody) -> dict:
    try:
        transfers.resolve(body.transaction_id, body.resolution, body.pair_with)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ---------- categories ----------

class CategoryBody(BaseModel):
    name: str


@app.get("/api/categories")
def categories() -> dict:
    with db.connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT c.id, c.name, c.is_active,"
                " (SELECT COUNT(*) FROM transactions t WHERE t.category_id = c.id) AS used"
                " FROM categories c ORDER BY c.is_active DESC, c.name"
            )
        ]
    return {"categories": rows}


@app.post("/api/categories")
def category_create(body: CategoryBody) -> dict:
    """Rule 2 is enforced by a database trigger, so a clash cannot slip through."""
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "A category needs a name")
    try:
        with db.connect() as conn:
            conn.execute("INSERT INTO categories (name) VALUES (?)", (name,))
            conn.commit()
    except sqlite3.IntegrityError as e:
        msg = str(e)
        if "share a name" in msg:
            raise HTTPException(
                400,
                f'"{name}" is the name of an account or a bank. An account is not a '
                "category, so pick a name that says what the money was for.",
            )
        raise HTTPException(400, f'"{name}" already exists')
    return {"ok": True}


@app.post("/api/categories/{category_id}/active")
def category_active(category_id: int, active: bool) -> dict:
    """
    Categories are never disabled into a dead end: an inactive one stays
    assignable, it just drops down the list.
    """
    with db.connect() as conn:
        conn.execute(
            "UPDATE categories SET is_active = ? WHERE id = ?", (1 if active else 0, category_id)
        )
        conn.commit()
    return {"ok": True}


class AssignBody(BaseModel):
    transaction_id: int
    category_id: int | None = None


@app.post("/api/transactions/category")
def assign_category(body: AssignBody) -> dict:
    with db.connect() as conn:
        conn.execute(
            "UPDATE transactions SET category_id = ?, updated_at = datetime('now')"
            " WHERE id = ?",
            (body.category_id, body.transaction_id),
        )
        conn.commit()
    return {"ok": True}


# ---------- what Eddie tells Claude ----------

# The options on a transaction. `counts` is what the wallet arithmetic does with
# it; the text is what the button says. Anything subtler goes in the free note.
TAGS = {
    "spending": "Counts as spending money",
    "not_spending": "Already budgeted, not spending money",
    "payback_spending": "A payback \u2014 put it back in my spending money",
    "payback_other": "A payback for something already budgeted",
    "covered": "Paid with money I set aside",
}


class NoteBody(BaseModel):
    transaction_id: int
    tag: str | None = None
    note: str | None = None


@app.get("/api/tags")
def tags() -> dict:
    return {"tags": [{"id": k, "label": v} for k, v in TAGS.items()]}


@app.post("/api/transactions/note")
def set_note(body: NoteBody) -> dict:
    """Eddie telling the app something the rules cannot work out on their own."""
    if body.tag is not None and body.tag not in TAGS:
        raise HTTPException(400, f"unknown tag {body.tag!r}")
    note = (body.note or "").strip() or None
    with db.connect() as conn:
        if body.tag is None and note is None:
            conn.execute("DELETE FROM transaction_notes WHERE transaction_id = ?", (body.transaction_id,))
        else:
            conn.execute(
                "INSERT INTO transaction_notes (transaction_id, tag, note, updated_at)"
                " VALUES (?, ?, ?, datetime('now'))"
                " ON CONFLICT(transaction_id) DO UPDATE SET"
                " tag = excluded.tag, note = excluded.note, updated_at = excluded.updated_at",
                (body.transaction_id, body.tag, note),
            )
        conn.commit()
        # Hand back the recomputed number so the page updates the moment he answers.
        pay = wallet.summary(conn)
    return {"ok": True, "pay": pay}


class MessageBody(BaseModel):
    body: str


@app.get("/api/messages")
def messages(limit: int = 20) -> dict:
    with db.connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT id, body, created_at FROM messages ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        ]
    return {"messages": rows}


@app.post("/api/messages")
def add_message(body: MessageBody) -> dict:
    text = body.body.strip()
    if not text:
        raise HTTPException(400, "nothing to send")
    with db.connect() as conn:
        conn.execute("INSERT INTO messages (body) VALUES (?)", (text,))
        conn.commit()
    return {"ok": True}


# ---------- connection health ----------

class ReconnectBody(BaseModel):
    institution_name: str | None = None
    item_id: int


@app.post("/api/link/token/update")
def link_token_update(body: ReconnectBody) -> dict:
    """Opens Plaid Link straight into the reconnect flow for one bank."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT access_token FROM items WHERE id = ?", (body.item_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "No such bank")
    try:
        return {"link_token": plaid_client.create_update_link_token(row["access_token"])}
    except Exception as e:
        raise HTTPException(502, f"Could not start the reconnect: {e}")


# ---------- envelopes ----------

class EnvelopeBody(BaseModel):
    name: str
    amount: float
    category_id: int | None = None
    pfc_match: str | None = None


@app.get("/api/envelopes")
def envelopes_list() -> dict:
    with db.connect() as conn:
        return envelopes.summary(conn)


@app.post("/api/envelopes")
def envelope_create(body: EnvelopeBody) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "An envelope needs a name")
    if body.amount < 0:
        raise HTTPException(400, "An envelope cannot be negative")
    try:
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO envelopes (name, amount, category_id, pfc_match)"
                " VALUES (?,?,?,?)"
                " ON CONFLICT(name) DO UPDATE SET amount = excluded.amount,"
                "   category_id = excluded.category_id, pfc_match = excluded.pfc_match,"
                "   is_active = 1",
                (name, body.amount, body.category_id, body.pfc_match),
            )
            conn.commit()
    except sqlite3.IntegrityError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/envelopes/{envelope_id}")
def envelope_delete(envelope_id: int) -> dict:
    with db.connect() as conn:
        conn.execute("DELETE FROM envelopes WHERE id = ?", (envelope_id,))
        conn.commit()
    return {"ok": True}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
