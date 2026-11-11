"""
Transfer detection. This is the heart of the app.

Rule 1: an expense happens once, at the moment of the charge. Paying the card
later is a transfer, so both legs must be excluded from spending.

Rule 3: an unmatched leg is surfaced, never swallowed. If only one half is
found, the app does not guess. It flags the row for review and says so.
"""

import uuid
from datetime import date

from . import db

# Plaid categories that suggest, but never prove, a transfer.
_TRANSFER_HINTS = {
    "TRANSFER_IN",
    "TRANSFER_OUT",
    "LOAN_PAYMENTS",
}
_CARD_PAYMENT_HINTS = {
    "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
    "TRANSFER_OUT_ACCOUNT_TRANSFER",
}

MATCH_WINDOW_DAYS = 3
# A real card payment can take longer than three days to show on both sides.
# The wider window is only allowed when BOTH legs carry a transfer-like category,
# because amount-and-date alone is not enough: on 2026-11-07 a $36.00 pub bill and
# a $44.54 vet bill each lined up exactly with an unrelated Zelle repayment days
# later, and a blanket five day window would have wrongly cancelled both out.
WIDE_WINDOW_DAYS = 5


def _as_date(s: str) -> date:
    return date.fromisoformat(str(s)[:10])


def _looks_like_transfer(row) -> bool:
    return (
        (row["pfc_primary"] or "") in _TRANSFER_HINTS
        or (row["pfc_detailed"] or "") in _CARD_PAYMENT_HINTS
    )


def detect(conn) -> dict:
    """
    Pair up transfer legs and flag the ones that have no partner.

    Match criteria, all required:
      equal absolute amount, dates within three days, opposite signs,
      and the two legs on different accounts.
    A credit leg paired with a depository leg is a card payment.
    Two depository legs are a self transfer.
    """
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT t.id, t.amount, t.date, t.account_id, t.pfc_primary, t.pfc_detailed,"
            "       a.type AS account_type"
            " FROM transactions t JOIN accounts a ON a.id = t.account_id"
            " WHERE t.is_transfer = 0 AND t.pending = 0"
            " ORDER BY t.date"
        )
    ]

    used: set[int] = set()
    pairs = 0

    for i, left in enumerate(rows):
        if left["id"] in used:
            continue
        for right in rows[i + 1 :]:
            if right["id"] in used or left["id"] == right["id"]:
                continue
            if left["account_id"] == right["account_id"]:
                continue
            # Opposite signs, equal size. Compare in cents to dodge float noise.
            if round(left["amount"] * 100) != -round(right["amount"] * 100):
                continue
            gap = abs((_as_date(left["date"]) - _as_date(right["date"])).days)
            both_look_like_transfers = _looks_like_transfer(left) and _looks_like_transfer(right)
            limit = WIDE_WINDOW_DAYS if both_look_like_transfers else MATCH_WINDOW_DAYS
            if gap > limit:
                continue

            types = {left["account_type"], right["account_type"]}
            if types == {"credit", "depository"}:
                kind = "card_payment"
            elif types == {"depository"}:
                kind = "self_transfer"
            else:
                continue  # Not a shape we are confident about.

            pair_id = uuid.uuid4().hex
            for leg in (left, right):
                conn.execute(
                    "UPDATE transactions SET is_transfer = 1, transfer_kind = ?,"
                    " transfer_pair_id = ?, needs_review = 0, review_reason = NULL"
                    " WHERE id = ?",
                    (kind, pair_id, leg["id"]),
                )
            used.add(left["id"])
            used.add(right["id"])
            pairs += 1
            break

    # Rule 3. Anything that still looks like a transfer but found no partner
    # goes to the review queue. It is never silently treated as spending.
    flagged = 0
    for row in rows:
        if row["id"] in used:
            continue
        if _looks_like_transfer(row):
            conn.execute(
                "UPDATE transactions SET needs_review = 1, review_reason = ?"
                " WHERE id = ? AND is_transfer = 0",
                ("Looks like a transfer but the matching leg was not found", row["id"]),
            )
            flagged += 1

    conn.commit()
    return {"pairs_matched": pairs, "flagged_for_review": flagged}


def run() -> dict:
    with db.connect() as conn:
        return detect(conn)


def resolve(transaction_id: int, resolution: str, pair_with: int | None = None) -> None:
    """
    The three ways a review item can be settled, per the brief:
      expense      - it really was spending
      transfer     - it moved to an account the app does not track
      reimbursement- money lent or repaid between people
      pair         - pair it by hand with a leg the matcher missed
    """
    with db.connect() as conn:
        if resolution == "expense":
            conn.execute(
                "UPDATE transactions SET needs_review = 0, review_reason = NULL,"
                " is_transfer = 0, transfer_kind = NULL WHERE id = ?",
                (transaction_id,),
            )
        elif resolution in ("transfer", "reimbursement"):
            kind = "reimbursement" if resolution == "reimbursement" else "self_transfer"
            conn.execute(
                "UPDATE transactions SET needs_review = 0, review_reason = NULL,"
                " is_transfer = 1, transfer_kind = ? WHERE id = ?",
                (kind, transaction_id),
            )
        elif resolution == "pair" and pair_with:
            pair_id = uuid.uuid4().hex
            conn.execute(
                "UPDATE transactions SET needs_review = 0, review_reason = NULL,"
                " is_transfer = 1, transfer_kind = 'card_payment', transfer_pair_id = ?"
                " WHERE id IN (?, ?)",
                (pair_id, transaction_id, pair_with),
            )
        else:
            raise ValueError(f"Unknown resolution: {resolution}")
        conn.commit()
