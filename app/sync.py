"""
Pulling transactions in from Plaid and storing them.

Two rules from the brief are enforced here:

Sign normalization happens on the way in, once, in db.normalize_amount. Nothing
downstream has to think about whether an account is a card or a checking account.

Pending charges are real money that is gone, so they are stored and counted like
any other charge. When a pending charge posts, Plaid removes the pending record
and sends a new posted one carrying pending_transaction_id. We match on that and
replace the row, keeping whatever category was already assigned.
"""

from . import db, plaid_client


def _account_map(conn) -> dict[str, tuple[int, str]]:
    """Plaid's account id -> (our row id, account type)."""
    return {
        r["plaid_account_id"]: (r["id"], r["type"])
        for r in conn.execute("SELECT id, plaid_account_id, type FROM accounts")
    }


def _store(conn, tx: dict, accounts: dict) -> bool:
    entry = accounts.get(tx.get("account_id"))
    if entry is None:
        return False  # An account we do not track. Skip rather than guess.
    account_row_id, _account_type = entry

    amount = db.normalize_amount(float(tx["amount"]))
    pending = 1 if tx.get("pending") else 0
    pending_ref = tx.get("pending_transaction_id")

    # If this posted row replaces a pending one, carry the category over and drop
    # the pending row, so the charge is never counted twice.
    carried_category = None
    if pending_ref:
        prior = conn.execute(
            "SELECT category_id FROM transactions WHERE plaid_transaction_id = ?",
            (pending_ref,),
        ).fetchone()
        if prior:
            carried_category = prior["category_id"]
            conn.execute(
                "DELETE FROM transactions WHERE plaid_transaction_id = ?", (pending_ref,)
            )

    pfc = tx.get("personal_finance_category") or {}
    conn.execute(
        "INSERT INTO transactions (plaid_transaction_id, account_id, amount, date,"
        " merchant_name, name, pending, pending_transaction_id, category_id,"
        " pfc_primary, pfc_detailed)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(plaid_transaction_id) DO UPDATE SET"
        "   amount = excluded.amount,"
        "   date = excluded.date,"
        "   merchant_name = excluded.merchant_name,"
        "   name = excluded.name,"
        "   pending = excluded.pending,"
        "   pending_transaction_id = excluded.pending_transaction_id,"
        "   pfc_primary = excluded.pfc_primary,"
        "   pfc_detailed = excluded.pfc_detailed,"
        "   updated_at = datetime('now')",
        (
            tx["transaction_id"],
            account_row_id,
            amount,
            str(tx["date"]),
            tx.get("merchant_name"),
            tx.get("name"),
            pending,
            pending_ref,
            carried_category,
            pfc.get("primary"),
            pfc.get("detailed"),
        ),
    )
    return True


def sync_item(item_row) -> dict:
    """Pull every page for one bank. Returns a small summary for the page to show."""
    added = modified = removed = 0
    cursor = item_row["sync_cursor"]
    access_token = item_row["access_token"]

    with db.connect() as conn:
        accounts = _account_map(conn)

        # A stale balance is worse than no balance, so ask Plaid how the
        # connection is before trusting anything it sends back.
        try:
            health = plaid_client.get_item_status(access_token)
        except Exception as e:
            health = {"healthy": False, "error_code": "ITEM_CHECK_FAILED",
                      "error_message": str(e)[:200]}
        if not health["healthy"]:
            conn.execute(
                "UPDATE items SET status = 'broken', last_sync_error = ? WHERE id = ?",
                (f"{health['error_code']}: {health['error_message']}"[:300], item_row["id"]),
            )
            conn.commit()
            raise RuntimeError(
                f"{item_row.get('institution_name') or 'Bank'} needs reconnecting "
                f"({health['error_code']})"
            )

        try:
            while True:
                a, m, r, cursor, has_more = plaid_client.sync_transactions(access_token, cursor)
                for tx in a:
                    added += _store(conn, tx, accounts)
                for tx in m:
                    modified += _store(conn, tx, accounts)
                for tx in r:
                    conn.execute(
                        "DELETE FROM transactions WHERE plaid_transaction_id = ?",
                        (tx["transaction_id"],),
                    )
                    removed += 1
                if not has_more:
                    break
            # Balances only arrived at link time before; refresh them every sync. The
            # cached balance from /accounts/get costs nothing extra on Plaid.
            try:
                for a in plaid_client.get_accounts(access_token):
                    b = a.get("balances") or {}
                    conn.execute(
                        "UPDATE accounts SET current_balance = ?, available_balance = ?,"
                        " last_synced_at = datetime('now') WHERE plaid_account_id = ?",
                        (b.get("current"), b.get("available"), a.get("account_id")),
                    )
            except Exception:
                pass  # a stale balance is shown with its date; transactions still count
            conn.execute(
                "UPDATE items SET sync_cursor = ?, last_sync_at = datetime('now'),"
                " status = 'healthy', last_sync_error = NULL WHERE id = ?",
                (cursor, item_row["id"]),
            )
            conn.commit()
        except Exception as e:
            conn.execute(
                "UPDATE items SET status = 'error', last_sync_error = ? WHERE id = ?",
                (str(e)[:300], item_row["id"]),
            )
            conn.commit()
            raise

    return {"added": added, "modified": modified, "removed": removed}


def sync_all() -> dict:
    with db.connect() as conn:
        items = [dict(r) for r in conn.execute("SELECT * FROM items")]
    totals = {"added": 0, "modified": 0, "removed": 0, "banks": 0, "errors": []}
    for item in items:
        try:
            out = sync_item(item)
            for k in ("added", "modified", "removed"):
                totals[k] += out[k]
            totals["banks"] += 1
        except Exception as e:
            totals["errors"].append(f"{item.get('institution_name') or 'bank'}: {e}")
    return totals
