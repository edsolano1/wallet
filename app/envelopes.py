"""
Earmarking, the brief's answer to "how much is actually mine to spend".

The brief bans budgets that hide money, but asks for exactly this:

    If Eddie wants money fenced off, use an earmarking concept that subtracts
    from spendable while remaining fully counted in net worth.

So an envelope only ever reduces SPENDABLE. Net worth never changes because of
one. Nothing is hidden; the arithmetic is shown line by line.

How a month works:
  - an envelope sets aside an amount, say $400 for groceries
  - grocery spending draws that envelope down
  - what is left in the envelope stays fenced off from the wallet
  - once the envelope is empty, further spending spills into the wallet,
    which is the behaviour he described: going over budget eats spending money
"""

from . import db


def _spent_clause() -> str:
    """Spending only: transfers are not spending, and refunds reduce the total."""
    return (
        " FROM transactions t"
        " WHERE t.is_transfer = 0"
        "   AND t.date >= date('now','start of month')"
        "   AND t.date <= date('now','start of month','+1 month','-1 day')"
    )


def summary(conn) -> dict:
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT e.id, e.name, e.amount, e.category_id, e.pfc_match, e.is_active,"
            " c.name AS category_name"
            " FROM envelopes e LEFT JOIN categories c ON c.id = e.category_id"
            " WHERE e.is_active = 1 ORDER BY e.amount DESC, e.name"
        )
    ]

    out = []
    fenced = 0.0
    overspill = 0.0
    for e in rows:
        params = []
        conds = []
        if e["category_id"]:
            conds.append("t.category_id = ?")
            params.append(e["category_id"])
        if e["pfc_match"]:
            # Works before anything is categorised by hand. Several categories can
            # be listed, comma separated, because one real envelope often spans
            # more than one of Plaid's labels: a Costco grocery run is filed as
            # GENERAL_MERCHANDISE_SUPERSTORES, not FOOD_AND_DRINK_GROCERIES, and a
            # groceries envelope that ignored that would miss most of the shopping.
            wanted = [x.strip() for x in e["pfc_match"].split(",") if x.strip()]
            if wanted:
                marks = ",".join("?" for _ in wanted)
                conds.append(f"(t.pfc_detailed IN ({marks}) OR t.pfc_primary IN ({marks}))")
                params += wanted + wanted

        if conds:
            spent = conn.execute(
                "SELECT COALESCE(SUM(-t.amount), 0) AS s" + _spent_clause()
                + " AND (" + " OR ".join(conds) + ")",
                params,
            ).fetchone()["s"]
        else:
            spent = 0.0

        spent = max(spent, 0.0)
        remaining = e["amount"] - spent
        still_fenced = max(remaining, 0.0)
        over = max(-remaining, 0.0)
        fenced += still_fenced
        overspill += over
        out.append(
            {
                **e,
                "spent": round(spent, 2),
                "remaining": round(remaining, 2),
                "still_fenced": round(still_fenced, 2),
                "overspent_by": round(over, 2),
            }
        )

    return {
        "envelopes": out,
        "total_set_aside": round(sum(e["amount"] for e in rows), 2),
        "total_still_fenced": round(fenced, 2),
        "total_overspent": round(overspill, 2),
    }
