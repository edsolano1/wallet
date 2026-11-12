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


def snapshot(conn, today: date) -> dict:
    s = wallet.summary(conn, today)
    start = date.fromisoformat(s["period_start"])
    three = today - timedelta(days=2)
    rows = wallet._rows(conn, min(start, three), today + timedelta(days=1))
    counted = [r for r in rows if wallet._is_spending(r)]

    def cost(rs):
        return round(wallet._cost(rs) - wallet._paid_back(rs), 2)

    counted.sort(key=lambda r: (r["date"], r["pending"]), reverse=True)
    charges = [{
        "merchant": r["what"] or "Charge",
        "amount": round(float(r["amount"]), 2),
        "date": r["date"],
        "category": PRETTY.get(r["pfc_primary"] or "", (r["pfc_primary"] or "").replace("_", " ").title()),
        "pending": bool(r["pending"]),
        "tip": round(wallet._tip(r), 2),
    } for r in counted[:25]]

    period = [r for r in counted if r["date"] >= start.isoformat() and not wallet._counts_as_payback(r)]
    needs = []
    for name, (budget, cats) in wallet.NEEDS.items():
        used = wallet._cost([r for r in period if (r["pfc_detailed"] or "") in cats])
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
        "banks": [{"name": b["name"], "ok": b["status"] == "healthy"} for b in banks],
        "updated": datetime.now(HOME).isoformat(timespec="seconds"),
    }


def run() -> None:
    from . import sync, transfers

    db.migrate()
    out = sync.sync_all()
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
