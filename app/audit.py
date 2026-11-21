"""
One-time spending check (his ask, 2026-11-20), paid from the plan's API credit, never his
Claude subscription. A strong model reads every transaction since Oct 1 next to the rules
the wallet uses, and lists what the rules get wrong: recurring charges that are really bills,
spending the wallet ignores, bills it counts as spending, and subscriptions worth a look.

    python -m app.audit          (in the "Spending check" workflow; needs ANTHROPIC_API_KEY)
    python -m app.audit --dry    (no model call: prints how big the request would be)

The report is sealed with DB_KEY into audit.md.enc; the public log shows only sizes and
token counts.
"""
import json
import os
import sqlite3
import sys
from datetime import date, timedelta

from app import cloud, config, wallet

MODEL = "claude-opus-5-5"
SINCE = date(2026, 10, 1)

RULES = """You are checking the rules of a personal spending app against two months of real bank data.

How the app works (the owner's own design; do not second-guess the design itself):
- He gets $784.62 of spending money per paycheck (every other Friday). Bills, rent, loans,
  subscriptions, parking, card payments, transfers between his own accounts and income never
  count against it. Groceries, gas, restaurants and ordinary purchases do.
- Paybacks (Zelles from people he split with) and card credits go back into spending money.
- Each transaction below shows whether the app currently COUNTS it as spending.

Find where the rules are wrong or missing. Report only real findings, most money first:
1. Recurring charges (same merchant, similar amount, monthly or every few weeks) that look like
   bills or subscriptions but are counted as spending, or are not in the bills list.
2. Things counted as spending that look like they shouldn't be (transfers to himself, bill
   payments, refunds, fees), and things NOT counted that look like real spending.
3. Subscriptions or repeat charges he might want to review or cancel, with the monthly cost.
4. Anything else odd: duplicate charges, possible fraud, refunds he's owed, large one-offs.

For each finding give: merchant, dates and amounts, what the app does now, what it should do,
and how sure you are. End with a 3-line summary of how much the spending number is off per
paycheck, if at all. Plain text, short, no tables wider than 4 columns. Don't pad: if a
section has nothing, say "Nothing found"."""


def _data(conn) -> str:
    today = date.today()
    masks = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT t.plaid_transaction_id, a.mask, a.type FROM transactions t JOIN accounts a ON a.id = t.account_id")}
    lines = ["date | account | amount (negative = money out) | merchant (bank text) | plaid category | app counts as spending | pending"]
    for r in sorted(wallet._rows(conn, SINCE, today + timedelta(days=1)), key=lambda r: r["date"]):
        if not r.get("pid"):
            continue  # an expected charge the app added itself, not a bank row
        mask, _ = masks.get(r["pid"], ("?", "?"))
        acct = wallet.NICKNAMES.get(mask, mask)
        raw = r.get("raw_name") or ""
        what = r["what"] or raw
        name = what if raw.lower() == (what or "").lower() else f"{what} ({raw})"
        counts = "yes" if wallet._is_spending(r) and not wallet._counts_as_payback(r) else (
            "payback" if wallet._is_spending(r) else "no")
        lines.append(f"{r['date']} | {acct} | {float(r['amount']):.2f} | {name} | "
                     f"{r['pfc_detailed'] or r['pfc_primary'] or ''} | {counts} | {'pending' if r['pending'] else ''}")
    bills = "\n".join(f"- {b['what']}: ${b['amount']:.2f}" + (" (on a card)" if b.get("card") else "") for b in wallet.BILLS)
    return (f"Bills the app knows about:\n{bills}\n\nSubscriptions it excludes by name: "
            f"{', '.join(wallet.SUBSCRIPTION_MERCHANTS)}\n\nTransactions since {SINCE.isoformat()}:\n" + "\n".join(lines))


def main(dry: bool) -> None:
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    data = _data(conn)
    print(f"request: {len(data)} characters, {data.count(chr(10))} lines")
    if dry:
        return
    import anthropic
    client = anthropic.Anthropic()
    with client.messages.stream(
        model=MODEL, max_tokens=16000, system=RULES,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": data}],
    ) as stream:
        msg = stream.get_final_message()
    report = "\n".join(b.text for b in msg.content if b.type == "text").strip()
    u = msg.usage
    receipt = (f"model {MODEL} · input tokens {u.input_tokens} · output tokens {u.output_tokens} · "
               f"stop {msg.stop_reason} · billed to the API credit (Console: Eddie's Individual Org)")
    print(receipt)  # counts only, safe for the public log
    body = f"# Spending check, {date.today().isoformat()}\n\n{report}\n\n---\n{receipt}\n"
    (config.DATABASE_PATH.parent / "audit.md.enc").write_bytes(cloud.seal(body.encode(), cloud._key("DB_KEY")))


if __name__ == "__main__":
    main("--dry" in sys.argv)
