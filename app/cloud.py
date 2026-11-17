"""
Runs the wallet in GitHub Actions, so the phone can read it without a login.

    python -m app.cloud open    finance.db.enc -> finance.db   (DB_KEY)
    python -m app.cloud run     sync Plaid, match transfers, write docs/wallet.enc (WALLET_KEY)
    python -m app.cloud seal    finance.db -> finance.db.enc   (DB_KEY)

Both files are AES-GCM encrypted, so the public repo and its public page show nothing.
The page decrypts wallet.enc with a key the phone keeps in the link's #fragment, which
browsers never send to a server.
"""

import base64
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import config, db, wallet

HOME = ZoneInfo("America/Phoenix")
ROOT = config.PROJECT_ROOT
SEALED_DB = ROOT / "finance.db.enc"
OUT = ROOT / "docs" / "wallet.enc"
# Money people owe him, sealed with DB_KEY so names and amounts stay out of the public repo.
# Edit owed.json (kept on this computer only), then `python -m app.cloud owe` to reseal it.
OWED_PLAIN = ROOT / "owed.json"
OWED = ROOT / "owed.enc"
# Under this much left, a request counts as paid (split rounding can leave a cent).
OWED_SLACK = 0.50


def _key(name: str) -> bytes:
    raw = os.environ[name].strip()
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def seal(data: bytes, key: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, data, None)


def unseal(blob: bytes, key: bytes) -> bytes:
    return AESGCM(key).decrypt(blob[:12], blob[12:], None)


PRETTY = {
    "FOOD_AND_DRINK": "Food & drink", "GENERAL_MERCHANDISE": "Shopping",
    "TRANSPORTATION": "Transportation", "ENTERTAINMENT": "Entertainment",
    "PERSONAL_CARE": "Personal care", "GENERAL_SERVICES": "Services",
    "HOME_IMPROVEMENT": "Home", "MEDICAL": "Medical", "TRAVEL": "Travel",
    "TRANSFER_IN": "Money back", "GOVERNMENT_AND_NON_PROFIT": "Government & giving",
}


def owed(conn) -> dict | None:
    return _owed(conn)[0]


def _owed(conn) -> tuple[dict | None, dict]:
    """
    What people still owe him, shown as possible money back, never counted in the wallet.

    Requests clear as Zelles come in on or after the day they were made, oldest first.
    A request naming someone only takes that person's Zelles; a split he answered in
    the app names no one (he may have split with anyone), so it takes any Zelle left
    over. The Zelles themselves already count as paybacks.
    """
    asks = []
    if OWED.exists() and "DB_KEY" in os.environ:
        asks = json.loads(unseal(OWED.read_bytes(), _key("DB_KEY")))
    # Claude's corrections after reading his notes: {"answer_for": <Plaid id>, "share": .., "who": .., "what": ..}
    fixes = {a["answer_for"]: a for a in asks if a.get("answer_for")}
    asks = [a for a in asks if not a.get("answer_for")]
    # Charges he answered "split" in the app: half is owed, tip estimate included
    # until the charge posts with the real tip.
    for r in conn.execute(
            "SELECT t.amount, t.pending, t.pfc_primary, t.pfc_detailed, t.is_transfer, t.transfer_kind,"
            " COALESCE(t.merchant_name, t.name) AS what, t.name AS raw_name, NULL AS tag,"
            " COALESCE(t.authorized_date, t.date) AS date, ans.who, ans.answered_on, ans.share,"
            " ans.plaid_transaction_id AS pid"
            " FROM answers ans JOIN transactions t ON t.plaid_transaction_id = ans.plaid_transaction_id"
            " WHERE ans.answer = 'split'"):
        r = dict(r)
        fix = fixes.get(r["pid"], {})
        if fix.get("share") is not None:
            r["share"] = fix["share"]
        r["who"] = fix.get("who", r["who"])
        if fix.get("what"):
            r["what"] = fix["what"]
        full = -float(r["amount"]) + wallet._tip(r)
        asks.append({"who": r["who"], "what": r["what"] or "Shared charge",
                     "amount": round(r["share"] if r["share"] is not None else full / 2, 2),
                     "since": r["answered_on"],
                     "note": "pending, tip estimated" if r["pending"] else ""})
    paid_for: dict[str, list[str]] = {}  # Zelle id -> what it paid for
    if not asks:
        return None, paid_for

    items = []
    def keep(a, left):
        if left >= OWED_SLACK:
            items.append({"who": a.get("who") or "", "what": a["what"], "amount": a["amount"],
                          "left": round(left, 2), "since": a["since"], "note": a.get("note", "")})

    # A card credit he is expecting (rewards redeemed as a statement credit) clears
    # when a credit of that amount lands on a card; it then counts as money back.
    for a in (a for a in asks if a.get("card")):
        landed = conn.execute(
            "SELECT 1 FROM transactions t JOIN accounts ac ON ac.id = t.account_id"
            " WHERE ac.type = 'credit' AND ABS(t.amount - ?) < 0.01 AND t.date >= ?",
            (a["amount"], a["since"])).fetchone()
        if not landed:
            keep(a, a["amount"])

    people = sorted((a for a in asks if not a.get("card")), key=lambda a: (not a.get("who"), a["since"]))
    if people:
        pool = [dict(r) for r in conn.execute(
            "SELECT plaid_transaction_id AS pid, LOWER(name) AS name, date, amount AS left FROM transactions"
            " WHERE amount > 0 AND date >= ? AND LOWER(name) LIKE '%zelle%' ORDER BY date",
            (min(a["since"] for a in people),))]
        for a in people:  # named requests first, then the open splits
            need = a["amount"]
            match = (a.get("match") or a.get("who") or "").lower()
            fits = [z for z in pool if z["date"] >= a["since"] and z["left"] > 0 and match in z["name"]]
            # A Zelle for exactly this amount is the one that paid it, even if another came first.
            fits.sort(key=lambda z: abs(z["left"] - need) >= 0.01)
            for z in fits:
                if need < OWED_SLACK:
                    break
                take = min(need, z["left"])
                z["left"] -= take
                need -= take
                paid_for.setdefault(z["pid"], []).append(a["what"])
            keep(a, need)
    return ({"total": round(sum(i["left"] for i in items), 2), "items": items} if items else None), paid_for


NOTES_SEEN = ROOT / "notes_seen.json"


def notes() -> None:
    """
    For Claude's hourly check, run on his computer: print the notes he typed in the
    popup that Claude hasn't handled yet, or "none". Reads the live database from
    the release, so it never touches the copy the workflow owns.
    """
    import sqlite3
    import tempfile
    import urllib.request

    blob = urllib.request.urlopen("https://github.com/edsolano1/wallet/releases/download/db/finance.db.enc").read()
    seen = set(json.loads(NOTES_SEEN.read_text())) if NOTES_SEEN.exists() else set()
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "f.db"
        path.write_bytes(unseal(blob, _key("DB_KEY")))
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT a.plaid_transaction_id AS id, a.answer, a.share, a.note, a.answered_on,"
            " COALESCE(t.merchant_name, t.name) AS merchant, t.amount, t.pending"
            " FROM answers a LEFT JOIN transactions t ON t.plaid_transaction_id = a.plaid_transaction_id"
            " WHERE a.note IS NOT NULL AND TRIM(a.note) <> ''")]
        conn.close()
    new = [r for r in rows if f'{r["id"]}|{r["note"]}' not in seen]
    print(json.dumps(new, indent=1) if new else "none")
    if "--mark" in sys.argv:
        NOTES_SEEN.write_text(json.dumps(sorted(seen | {f'{r["id"]}|{r["note"]}' for r in rows})))


def answer(raw: str) -> None:
    """
    Store answers sent from the page: a JSON list of {"id", "answer", "who"?, "note"?}.
    The note is for Claude to read; it never changes the arithmetic.
    It arrives as a workflow input, so anything malformed is dropped, not trusted.
    """
    try:
        given = json.loads(raw or "[]")
    except ValueError:
        return
    db.migrate()
    with db.connect() as conn:
        for a in given if isinstance(given, list) else []:
            if not isinstance(a, dict) or a.get("answer") not in ("split", "solo", "settled"):
                continue
            pid = str(a.get("id", ""))[:80]
            if not conn.execute("SELECT 1 FROM transactions WHERE plaid_transaction_id = ?", (pid,)).fetchone():
                continue
            who = str(a["who"])[:40] if a.get("who") and a["answer"] == "split" else None
            note = str(a.get("note") or "").strip()[:500] or None
            try:  # what they owe, when it isn't half
                share = round(float(a["share"]), 2) if a.get("share") not in (None, "") and a["answer"] == "split" else None
            except (TypeError, ValueError):
                share = None
            if share is not None and not 0 < share < 10000:
                share = None
            conn.execute("INSERT INTO answers (plaid_transaction_id, answer, who, answered_on, note, share)"
                         " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(plaid_transaction_id) DO UPDATE SET"
                         " answer = excluded.answer, who = excluded.who, share = excluded.share,"
                         " note = COALESCE(excluded.note, answers.note)",
                         (pid, a["answer"], who, datetime.now(HOME).date().isoformat(), note, share))
        conn.commit()


def snapshot(conn, today: date) -> dict:
    s = wallet.summary(conn, today)
    start = date.fromisoformat(s["period_start"])
    three = today - timedelta(days=2)
    rows = wallet._rows(conn, min(start, three), today + timedelta(days=1))
    counted = [r for r in rows if wallet._is_spending(r)]

    def cost(rs):
        return round(wallet._cost(rs) - wallet._paid_back(rs), 2)

    counted.sort(key=lambda r: (r["date"], r["pending"]), reverse=True)
    owing, paid_for = _owed(conn)

    def label(r):
        """A Zelle back says who sent it and what for, not the bank's raw line."""
        m = re.search(r"zelle payment from ([a-z]+)", r["what"] or "", re.I)
        if float(r["amount"]) <= 0 or not m:
            return r["what"] or "Charge", None
        why = paid_for.get(r["pid"])
        return f"Zelle from {m.group(1).title()}", "Paid back · " + (" + ".join(why) if why else "what for? tell Claude")

    charges = [{
        "merchant": label(r)[0],
        "amount": round(float(r["amount"]), 2),
        "date": r["date"],
        "category": label(r)[1] or ("Gas" if wallet.is_gas_station(r) and abs(float(r["amount"])) >= wallet.GAS_MIN_FILLUP
                    else PRETTY.get(r["pfc_primary"] or "", (r["pfc_primary"] or "").replace("_", " ").title())),
        "pending": bool(r["pending"]),
        "tip": round(wallet._tip(r), 2),
    } for r in counted[:25]]

    period = [r for r in counted if r["date"] >= start.isoformat() and not wallet._counts_as_payback(r)]
    ask_from = max(wallet.SPLIT_ASK_FROM, today - timedelta(days=14)).isoformat()
    questions = [{"id": r["pid"], "merchant": r["what"] or "Charge", "date": r["date"], "why": why,
                  "amount": round(-float(r["amount"]) + wallet._tip(r), 2), "pending": bool(r["pending"])}
                 for r in wallet._rows(conn, date.fromisoformat(ask_from), today + timedelta(days=1))
                 if (why := wallet.split_question(r))]
    needs = []
    for name, (budget, cats) in wallet.NEEDS.items():
        if name == "Gas":  # small gas-station charges are snacks, not fill-ups
            hits = [r for r in period if wallet.is_gas_station(r) and abs(float(r["amount"])) >= wallet.GAS_MIN_FILLUP]
        else:
            hits = [r for r in period if (r["pfc_detailed"] or "") in cats]
        used = wallet._cost(hits)
        needs.append({"name": name, "budget": budget, "spent": round(used, 2),
                      "left": round(max(budget - used, 0), 2)})
    still_needed = sum(n["left"] for n in needs)

    saved = sum(a for day, a in wallet.SET_ASIDE.items() if day <= today)
    planned = sum(wallet.SET_ASIDE.values())
    used_fund = sum(a for _, a in wallet.FUND_SPENT)
    fund = {"name": "Black Friday Fund", "saved": saved, "planned": planned,
            "spent": round(used_fund, 2), "left": round(saved - used_fund, 2),
            "left_planned": round(planned - used_fund, 2),
            "items": [{"what": w, "amount": a} for w, a in wallet.FUND_SPENT]}

    banks = [dict(r) for r in conn.execute(
        "SELECT institution_name AS name, status, last_sync_at, last_sync_error FROM items")]
    return {
        **s,
        "today": today.isoformat(),
        "spent_today": cost([r for r in counted if r["date"] == today.isoformat()]),
        "spent_3d": cost([r for r in counted if r["date"] >= three.isoformat()]),
        "charges": charges,
        "needs": needs,
        "accounts": [{"name": r["name"], "mask": r["mask"], "type": r["type"],
                      "balance": r["available_balance"] if r["type"] == "depository" and r["available_balance"] is not None else r["current_balance"],
                      "as_of": r["last_synced_at"]}
                     for r in conn.execute(
                         "SELECT name, mask, type, current_balance, available_balance, last_synced_at"
                         " FROM accounts WHERE type = 'depository' OR COALESCE(current_balance, 0) <> 0"
                         " ORDER BY type, current_balance DESC")],
        "moves": [{"date": r["date"], "amount": round(float(r["amount"]), 2), "what": r["what"], "account": r["acct"]}
                  for r in conn.execute(
                      "SELECT t.date, t.amount, COALESCE(t.merchant_name, t.name) AS what, a.name || ' ' || a.mask AS acct"
                      " FROM transactions t JOIN accounts a ON a.id = t.account_id"
                      " WHERE t.date >= ? AND t.transfer_kind = 'self_transfer' AND t.amount < 0"
                      " ORDER BY t.date DESC LIMIT 8", ((today - timedelta(days=21)).isoformat(),))],
        "free": round(s["left"] - still_needed, 2),
        "fund": fund,
        "owed": owing,
        "questions": questions,
        "banks": [{"name": b["name"], "ok": b["status"] == "healthy"} for b in banks],
        "updated": datetime.now(HOME).isoformat(timespec="seconds"),
    }


def run() -> None:
    from . import sync, transfers

    db.migrate()
    out = sync.sync_all()
    sync.backfill_purchase_dates()
    out["transfers"] = transfers.run()
    print(json.dumps({k: v for k, v in out.items() if k != "transfers"}))  # counts only, no money
    with db.connect() as conn:
        data = snapshot(conn, datetime.now(HOME).date())
    data["errors"] = out["errors"]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(base64.b64encode(seal(json.dumps(data).encode(), _key("WALLET_KEY"))).decode())


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "open":
        config.DATABASE_PATH.write_bytes(unseal(SEALED_DB.read_bytes(), _key("DB_KEY")))
    elif cmd == "seal":
        SEALED_DB.write_bytes(seal(config.DATABASE_PATH.read_bytes(), _key("DB_KEY")))
    elif cmd == "run":
        run()
    elif cmd == "answer":
        answer(os.environ.get("ANSWERS", ""))
    elif cmd == "notes":
        notes()
    elif cmd == "owe":
        OWED.write_bytes(seal(OWED_PLAIN.read_bytes(), _key("DB_KEY")))
