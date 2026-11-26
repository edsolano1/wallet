"""
The spending money number, as Eddie actually defines it.

This mirrors his "Eddie's Wallet" page, which until now read from Monarch. The
whole point of this project is to stop paying Monarch, so the same arithmetic
runs here on Plaid data instead.

His model, in his words, is NOT "balance minus commitments". It is an allowance:

  - $1,700 a month of flexible spending money. That is his $1,905 budget less
    $175 of subscriptions and the $30 parking spot, both of which are bills.
  - Paid every other Friday, so each paycheck carries 1700 * 12 / 26 = $784.62.
  - Rent, student loans, insurance, subscriptions, the parking spot, card
    payments, transfers and paybacks NEVER count against it. They are covered
    before this number exists.
  - Gas, groceries, restaurants and the rest of day to day life DO count.
  - Whatever is left at the end of a pay period rolls into the next one, and an
    overspend is carried forward as a debt against the next one.
  - Money set aside for the Black Friday Fund comes off the top.
"""

import re
from datetime import date, timedelta

from . import db

MONTHLY_BUDGET = 1700.00
PER_PERIOD = MONTHLY_BUDGET * 12 / 26          # 784.62, fortnightly pay
PAY_ANCHOR = date(2026, 10, 30)                # a known payday, Vertex pays alternate Fridays
TIP_RATE = 0.20
TIP_MIN = 30.00

# Set aside off the top, by the payday that period starts on.
# Refill from the Nov 27 paycheck's spending money (Nov 24): he's buying the Black Friday clothes,
# board, heater and bins now and letting the fund go negative; the cushion stays untouched.
# Exact: the orders came to $224.44 against $114.45 left.
# Nov 26 (his answer): $450 more ON TOP of that, for more Black Friday buys: one $559.99 transfer.
# Spending money goes negative this paycheck; he chose that.
FUND_REFILLS = [(date(2026, 11, 27), 559.99)]
SET_ASIDE = {date(2026, 10, 30): 425.00, date(2026, 11, 13): 425.00, **{d: x for d, x in FUND_REFILLS}}
SET_ASIDE_LABEL = "for the Black Friday Fund"
# The $425 a paycheck that went to Black Friday stays in spending money from Nov 27 on, unassigned,
# until he picks a home for it (his words, Nov 24). Set "home" when he decides; the flag then goes.
FREED = {"amount": 425.00, "from": date(2026, 11, 27), "home": None}

# When a category ends, the app asks where its money goes (his feature, Nov 24): the money left in it
# and the per-paycheck amount it used to take. Answers live in `category_choices`; "later" re-asks
# each payday. A route other than "wallet" comes off the top of each paycheck from the one he answered in.
ENDED = [{"id": "black-friday", "name": "Black Friday", "per_check": FREED["amount"], "from": FREED["from"], "left": 0.0}]
ROUTES = {"loan": "Student loan saving", "cushion": "Cushion", "ally": "Ally Savings", "wallet": "Keep in spending money"}
ROUTE_TO = {"loan": ("Ally Savings ··2617", r"ally"), "cushion": ("Chase Savings ··6725", r"to sav|6725"),
            "ally": ("Ally Savings ··2617", r"ally")}


def category_choices(conn) -> dict:
    try:
        return {r["id"]: dict(r) for r in conn.execute("SELECT * FROM category_choices")}
    except Exception:  # an older database without the table
        return {}


def routed(conn, payday: date) -> list[dict]:
    """Per-paycheck money from ended categories he sent somewhere, for the paycheck starting `payday`."""
    ch, out = category_choices(conn), []
    for e in ENDED:
        c = ch.get(e["id"])
        if not c or c["future"] not in ROUTE_TO:
            continue
        if payday >= max(e["from"], period_for(date.fromisoformat(c["answered_on"]))[0]):
            out.append({"id": e["id"], "what": ROUTES[c["future"]], "from_name": e["name"], "amount": e["per_check"],
                        "to": ROUTE_TO[c["future"]][0], "pattern": ROUTE_TO[c["future"]][1]})
    return out


def ended_open(conn) -> list[dict]:
    """Ended categories still waiting for his answer."""
    ch = category_choices(conn)
    return [{"id": e["id"], "name": e["name"], "per_check": e["per_check"], "left": e["left"], "routes": ROUTES}
            for e in ENDED if e["id"] not in ch]

# The Black Friday Fund as he runs it (his update, 2026-11-20). The real money is the $450 he
# moved Chase -> Ally Spending on Nov 16 and back to Chase checking on Nov 20 (lands Nov 23);
# that transfer is not spending money. The first $450 bought the monitor and is done.
# SET_ASIDE above is only what came off his spending money; this is what the fund holds.
# Plus $75 he added on Nov 22 from unassigned checking (to Chase Savings) for two more items.
FUND_SAVED = 525.00
FUND_SPENT = [
    # Two Amazon orders on Nov 20 (order emails): 114-5226929-2324258 and 114-8570827-7062625.
    ("Trimmer, Amazon", 104.73),
    ("Feetures quarter socks, 3-pack, Amazon", 38.56),
    ("Prime, one month (ends Dec 20)", 14.99),
    # Order sp-5798420, $37.64: $34.68 on a MicroPerfumes gift card, the rest on Sapphire.
    ("MicroPerfumes, after the gift card", 2.96),
    # Order 114-3562857-5901814 (Nov 24), Prime Visa: $43.20 + $3.93 tax, charged when it ships.
    ("Feetures crew socks, 3 pairs, Amazon", 47.13),
    # Nov 24, Prime Visa, $224.44 in four Amazon orders charged as they ship; tax split by price.
    ("SoliSnug desk heater (business expense), Amazon", 68.72),  # 114-5359773-1945839
    ("RAINHOL ironing board, Amazon", 52.85),                    # 114-5359773-1945839
    ("Rolling under-bed bins, 2-pack, Amazon", 36.16),           # 114-0739792-1372252
    ("UA Storm Liner gloves, Amazon", 17.58),                    # 114-0739792-1372252
    ("UA Velocity quarter-zip, Amazon", 29.55),                  # 114-9989667-9882661
    ("UA Tech 2.0 half-zip, Amazon", 19.58),                     # 114-3420614-6446634, arrives Dec 1
    # Nov 26, logitech.com order US05227819 (refurbished): $89.99 - $18 educator code + $6.55 tax, on a Visa.
    ("Logitech MX Master 4 mouse (business expense), Logitech", 78.54),
]
# Ordered but charged only when it ships: kept out of what's left so the fund doesn't look bigger.
FUND_HELD = [("Philips i9000 shaver", 218.18)]
# Money back into the fund that he has confirmed. The ~$16 Amazon card overpayment credit
# was used against the $143.30 Amazon order (confirmed Nov 20).
FUND_CREDITS = [("Amazon card credit, used on the order", 16.00)]
# Money he expects back into the fund but hasn't seen land.
FUND_CREDIT_TO_CONFIRM = []
# Bought with the first $450 (closed), shown for the record only; not in the $525.
# Receipt Nov 8: $709.14 = $409.14 on the Samsung card + $300 cash from the Costco returns.
FUND_EARLIER = [("Acer X32 X monitor, Micro Center", 709.14, "$409.14 on the Samsung card + $300 cash")]
# Watching for a price (from the Shopping chat, Nov 23): (what, price now, note). From the fund unless noted.
FUND_WATCH = [
    ("Acer X32 X monitor", 649.99, "Paid $709.14 at Micro Center. Amazon is $649.99 now; if it drops below that, reorder on Amazon and return by Dec 8."),
    ("Philips i9000 shaver", 199.99, "Ordered on Amazon for $218.19; switch if it's under $199.99 elsewhere."),
    ("Babbel Lifetime", 199.20, "Buy at $159 or less. Probably won't fit the fund."),
    ("Feetures Crew socks, 3 more pairs", 43.20, "Wait for the January sale; buy if it beats $14.40 a pair."),
]
# The fund is temporary: its home tile goes away after this day.
FUND_SHOW_UNTIL = date(2027, 1, 31)
# Whatever is left in the fund goes back into spending money (his rule, Nov 26). It closes on the
# first payday after the Jan 31 return window, so late refunds land in the fund first.
FUND_CLOSE = date(2027, 2, 5)


def fund_left(today: date) -> float:
    """What the fund has left: saved plus refills so far, less spent and held, plus money back."""
    saved = FUND_SAVED + sum(x for d, x in FUND_REFILLS if d <= today)
    return (saved - sum(a for _, a in FUND_SPENT) - sum(a for _, a in FUND_HELD)
            + sum(a for _, a in FUND_CREDITS))

# Needs, not wants (his words, 2026-11-11): groceries and gas have to happen every month,
# so each gets a soft envelope per paycheck. They are INSIDE spending money, not on top of
# it; the page shows how much of each is left and what is free once they are covered.
# Costco and Walmart are filed as superstores, so they count as groceries.
# Gas-station charges under this are snacks; only bigger ones count against the Gas need.
GAS_MIN_FILLUP = 20.00
# Gas stations the bank labels as something else. Morongo's travel center on I-10 in
# Cabazon has slot machines, so its pumps come through as "casinos and gambling".
GAS_STATIONS_LIKE = ("morongo band of missio",)


def is_gas_station(row) -> bool:
    if (row.get("pfc_detailed") or "") == "TRANSPORTATION_GAS":
        return True
    raw = ((row.get("raw_name") or "") + " " + (row.get("what") or "")).lower()
    return any(x in raw for x in GAS_STATIONS_LIKE)

# Online grocery orders the bank files elsewhere (Beyond's Shopify store).
# Watermill Express: drinking water. Its machines bill through Nayax, the card reader company.
GROCERY_LIKE = ("beyond plant", "beyond meat", "beyondmeat", "watermill", "nayax")

# Pending holds bigger than the real charge: (merchant fragment, hold, what it really costs).
# Watermill Express holds $3.00 and settles at $1.75.
HOLDS = [("watermill", 3.00, 1.75), ("nayax", 3.00, 1.75)]


def _real_amount(row: dict) -> dict:
    """Swap a known pending hold for what the charge will actually settle at."""
    if row.get("pending"):
        name = ((row.get("what") or "") + " " + (row.get("raw_name") or "")).lower()
        for frag, hold, real in HOLDS:
            if frag in name and abs(-float(row["amount"]) - hold) < 0.01:
                row["amount"] = -real
    return row


# Refunds that go back into the groceries money, not the wallet (his call): matched by store name
# or, if the bank names it oddly, by card and exact amount. (fragment, card, amount, earliest day)
NEEDS_REFUNDS = [
    ("beyond", "9203", 69.93, date(2026, 11, 24)),  # order #47138, thawed in shipping; Shopify says up to 10 days
]


def _is_needs_refund(row) -> bool:
    if float(row["amount"]) <= 0:
        return False
    name = ((row.get("what") or "") + " " + (row.get("raw_name") or "")).lower()
    return any(abs(float(row["amount"]) - amt) < 0.01 and row["date"] >= after.isoformat()
               and (frag in name or row.get("mask") == card) for frag, card, amt, after in NEEDS_REFUNDS)


def is_grocery(row) -> bool:
    if _is_needs_refund(row):
        return True
    if (row.get("pfc_detailed") or "") in NEEDS["Groceries"][1]:
        return True
    raw = ((row.get("raw_name") or "") + " " + (row.get("what") or "")).lower()
    if any(x in raw for x in GROCERY_LIKE):
        return True
    # Small gas-station charges are snacks and drinks: food, so they count as groceries (Nov 23).
    return is_gas_station(row) and abs(float(row.get("amount") or 0)) < GAS_MIN_FILLUP


# Charges he made that the bank hasn't sent yet (found in an order email), counted
# now so the number is honest. Each drops out on its own once a real charge of the
# same amount shows up on that card within a week.
EXPECTED = [
    {"id": "beyond-47138", "what": "Beyond (online order)", "amount": 120.35, "date": date(2026, 11, 16),
     "mask": "9203", "pfc_primary": "FOOD_AND_DRINK", "pfc_detailed": "FOOD_AND_DRINK_GROCERIES"},
    # Was pending on Samsung on Nov 15, then dropped out of the feed; it posts with the tip.
    {"id": "ramen-1115", "what": "Ramen Hub & Sushi", "amount": 38.79, "date": date(2026, 11, 15), "tol": 12.00, "like": "ramen",
     "mask": "1271", "pfc_primary": "FOOD_AND_DRINK", "pfc_detailed": "FOOD_AND_DRINK_RESTAURANT"},
]

# ---- Upcoming bills: cash in checking against what has to come out before payday ----
# The account bills and card payoffs come out of.
BILLS_ACCOUNT = "7372"
# Cards whose reported balance already includes pending charges (most report posted only).
BALANCE_HAS_PENDING = {"2098"}
# His bills, from his Monarch budget and what has actually billed. `day` is the day of the
# month it usually lands, `every`+`anchor` a fixed cycle, `once` a one-off. A bill counts as
# paid when a charge matching `match` (a regex on the merchant) for between lo and hi lands
# from 12 days before to 5 days after it's due. `card` bills land on a card, not checking.
# `sum` bills are paid in pieces (gas and electric go to Denisse as two Zelles, $11.28 and
# $43.15 on Nov 13), so every matching payment up to `each_hi` in that window adds up.
BILLS = [
    {"what": "Rent", "amount": 542.00, "day": 20, "match": r"zelle payment to denisse", "lo": 541, "hi": 543},
    {"what": "Gas & electric (your half)", "amount": 110.00, "day": 16, "match": r"zelle payment to denisse", "lo": 30, "hi": 250,
     "sum": True, "each_hi": 200, "est": True},
    {"what": "Verizon", "amount": 101.90, "day": 19, "match": r"verizon", "lo": 80, "hi": 140},
    {"what": "Anthropic", "amount": 109.10, "day": 20, "match": r"anthropic", "lo": 15, "hi": 250, "card": True},
    {"what": "Spotify", "amount": 14.17, "day": 24, "match": r"spotify", "lo": 5, "hi": 30, "card": True},
    # Two Ring plans, $10.90 each, both his (not split). Paid once both have landed.
    {"what": "Ring (two plans)", "amount": 21.80, "day": 1, "match": r"^ring\b", "lo": 20, "hi": 25,
     "sum": True, "each_lo": 5, "each_hi": 15, "card": True},
    # From Ally Spending after Nov 28 (paid with the $350 a paycheck sent to Ally), so checking only owes this one.
    # Nov 28 at the $327.26 minimum (his call, Nov 25: get his bearings this month, ramp up after).
    {"what": "Student loan autopay", "amount": 327.26, "day": 28, "start": date(2026, 11, 28), "end": date(2026, 11, 30), "match": r"aidvantage|dept ?of ?ed|nelnet|mohela", "lo": 300, "hi": 760},
    # Last payment Dec 6: GEICO starts Dec 26, paid from Ally Savings and paid back monthly.
    {"what": "State Farm", "amount": 253.80, "day": 6, "match": r"state farm", "lo": 200, "hi": 270, "end": date(2026, 12, 31)},
    {"what": "Parking", "amount": 30.00, "day": 9, "match": r"cash app\*gee", "lo": 25, "hi": 35},
    {"what": "Car wash", "amount": 21.00, "every": 28, "anchor": date(2026, 11, 13), "match": r"cobblestone", "lo": 15, "hi": 30, "card": True},
]
# His names for his accounts.
NICKNAMES = {"7372": "Chase Checking", "9203": "Sapphire", "2098": "MGM Card", "1271": "Samsung Card", "4713": "Amazon Card"}
LATER_MIN = 200.00  # past the next payday, only bills this big are worth a heads-up


def _due_dates(b, start: date, end: date) -> list[date]:
    if "once" in b:
        return [b["once"]] if start <= b["once"] < end else []
    if "every" in b:
        k = max(0, -(-(start - b["anchor"]).days // b["every"]))
        out, d = [], b["anchor"] + timedelta(days=k * b["every"])
        while d < end:
            out.append(d)
            d += timedelta(days=b["every"])
        return out
    out, y, m = [], start.year, start.month
    while date(y, m, 1) < end:
        d = date(y, m, min(b["day"], 28))
        if start <= d < end and b.get("start", d) <= d <= b.get("end", d):
            out.append(d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def upcoming_bills(conn, today: date, s: dict, owed_total: float) -> dict:
    """Checking, less every card balance (pending and not-yet-sent charges included) and
    the bills due before the next payday, then the same for the pay cycle after."""
    start, nxt = period_for(today)
    after = nxt + timedelta(days=14)
    acct = conn.execute("SELECT name, mask, COALESCE(available_balance, current_balance) AS bal FROM accounts"
                        " WHERE mask = ?", (BILLS_ACCOUNT,)).fetchone()
    cash = float(acct["bal"] or 0) if acct else 0.0

    cards = []
    for a in conn.execute("SELECT id, name, official_name, mask, current_balance FROM accounts WHERE type = 'credit'"):
        pend = [dict(r) for r in conn.execute(
            "SELECT amount, pfc_primary, pfc_detailed, COALESCE(merchant_name, name) AS what, name AS raw_name,"
            " 1 AS pending FROM transactions WHERE account_id = ? AND pending = 1", (a["id"],))]
        held = sum(float(p["amount"]) for p in pend)
        pend = [_real_amount(p) for p in pend]
        owe = float(a["current_balance"] or 0)
        if a["mask"] not in BALANCE_HAS_PENDING:
            owe -= held
        owe -= sum(float(p["amount"]) for p in pend) - held  # a hold that will settle smaller
        owe += sum(_tip(p) for p in pend)
        waiting = [e for e in _expected(conn, today - timedelta(days=21), today + timedelta(days=1)) if e["mask"] == a["mask"]]
        owe += sum(-float(e["amount"]) + _tip(e) for e in waiting)
        if abs(owe) >= 0.01:
            cards.append({"name": NICKNAMES.get(a["mask"], a["official_name"] or a["name"]), "mask": a["mask"], "owe": round(owe, 2),
                          "pending": len(pend) + len(waiting)})
    cards.sort(key=lambda c: -c["owe"])
    card_total = round(sum(c["owe"] for c in cards), 2)

    lands = [dict(r) for r in conn.execute(
        "SELECT t.plaid_transaction_id AS pid, -t.amount AS amt, COALESCE(t.authorized_date, t.date) AS day,"
        " LOWER(COALESCE(t.merchant_name, '') || ' | ' || t.name) AS what FROM transactions t"
        " WHERE t.amount < 0 AND COALESCE(t.authorized_date, t.date) >= ?", ((start - timedelta(days=20)).isoformat(),))]
    used = set()

    def cycle(a: date, b: date) -> list[dict]:
        items = []
        for bill in BILLS:
            for due in _due_dates(bill, a, b):
                lo, hi = (due - timedelta(days=12)).isoformat(), (due + timedelta(days=5)).isoformat()
                like = [t for t in lands if t["pid"] not in used and lo <= t["day"] <= hi
                        and any(re.search(bill["match"], part.strip()) for part in t["what"].split("|"))]
                if bill.get("sum"):
                    parts = [t for t in like if t["amt"] <= bill["each_hi"]]
                    total = sum(t["amt"] for t in parts)
                    hit = {"amt": total} if bill["lo"] <= total <= bill["hi"] else None
                    if hit:
                        used.update(t["pid"] for t in parts)
                else:
                    hit = next((t for t in like if bill["lo"] <= t["amt"] <= bill["hi"]), None)
                    if hit:
                        used.add(hit["pid"])
                # Rent and the loan wait in Chase Savings until he moves them back for the bill.
                saved = (bill["what"] in SAVED_BILLS and due >= SAVED_FROM and not hit
                         and not _moved(conn, BILLS_ACCOUNT, r"from sav|6725", SAVED_BILLS[bill["what"]] - 1,
                                        due - timedelta(days=10), today, 1))
                items.append({"what": bill["what"], "due": due.isoformat(), "card": bool(bill.get("card")),
                              "amount": round(hit["amt"] if hit else bill["amount"], 2), "paid": bool(hit),
                              "est": bool(bill.get("est")) and not hit, "saved": saved})
        # Each payday's two moves out of checking count like bills until they're made.
        for payday in (a + timedelta(days=14 * k) for k in range(3) if a + timedelta(days=14 * k) < b):  # a is a payday
            if payday >= MOVES_FROM and not _bonus_check(payday):
                for m in [m for m in money_moves(conn, payday) if m["kind"] == "payday"]:
                    items.append({"what": "To " + ("Chase Savings" if m["id"].endswith("chase") else "Ally"),
                                  "due": payday.isoformat(), "card": False, "amount": m["amount"],
                                  "paid": m["done"] and payday <= today, "est": False, "saved": False, "move": True})
        items.sort(key=lambda i: i["due"])
        return items

    now_items, next_items = cycle(start, nxt), cycle(nxt, after)
    later = [i for i in cycle(after, after + timedelta(days=28)) if i["amount"] >= LATER_MIN and not i.get("move")]
    due_now = sum(i["amount"] for i in now_items if not i["paid"] and not i["saved"])
    spend_now = max(s["left"], 0.0)  # what's still his to spend before payday
    left_now = cash - card_total - due_now - spend_now

    pay = conn.execute("SELECT amount FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE a.mask = ?"
                       " AND t.pfc_detailed = 'INCOME_SALARY' AND t.amount > 0 ORDER BY t.date DESC LIMIT 1",
                       (BILLS_ACCOUNT,)).fetchone()
    paycheck = float(pay["amount"]) if pay else 0.0
    spend_next = PER_PERIOD - SET_ASIDE.get(nxt, 0.0) - sum(r["amount"] for r in routed(conn, nxt))
    due_next = sum(i["amount"] for i in next_items if not i["paid"] and not i["saved"])
    left_next = left_now + paycheck - due_next - spend_next
    return {
        "account": f'{NICKNAMES.get(acct["mask"], acct["name"].title())} ··{acct["mask"]}' if acct else "Checking",
        "cash": round(cash, 2), "cards": cards, "card_total": card_total,
        "now": {"from": start.isoformat(), "to": (nxt - timedelta(days=1)).isoformat(), "items": now_items,
                "due": round(due_now, 2), "spend": round(spend_now, 2), "left": round(left_now, 2),
                "left_if_owed": round(left_now + owed_total, 2)},
        "next": {"from": nxt.isoformat(), "to": (after - timedelta(days=1)).isoformat(), "items": next_items,
                 "paycheck": round(paycheck, 2), "due": round(due_next, 2), "spend": round(spend_next, 2),
                 "left": round(left_next, 2), "left_if_owed": round(left_next + owed_total, 2)},
        "later": later,
    }


NEEDS = {
    # $225 since Nov 23: $175 ran short (Aug-Nov paychecks ran $38 to $268, most over $200).
    "Groceries": (225.00, {"FOOD_AND_DRINK_GROCERIES", "GENERAL_MERCHANDISE_SUPERSTORES"}),
    "Gas": (75.00, {"TRANSPORTATION_GAS"}),
}

# Driving trips: their gas is added to the Gas budget of the paycheck they fall in.
# Check his calendar and email for trips he'll drive to and add them here.
# (first day, last day, what, gas for the trip)
TRIPS = [
    (date(2026, 11, 15), date(2026, 11, 16), "California drive", 100.00),  # fill-ups $95.65
]


def trip_gas(start: date, end: date, today: date, spent_on=lambda a, b: 0.0) -> list[tuple[str, float]]:
    """Trips whose first day falls in [start, end), with the gas to budget for each.
    A trip still ahead (or under way) gets its full estimate. Once it's over, it only
    covers the gas it really used, so nothing is left "still needed" for it (his call, Nov 23)."""
    out = []
    for a, b, w, g in TRIPS:
        if start <= a < end:
            out.append((w, round(min(g, spent_on(a, b)), 2) if b < today else g))
    return out

# ---- Groceries & gas envelope (his rules, Nov 23) --------------------------------------
# From the Nov 27 paycheck on, groceries and gas come off the top of every paycheck like an
# envelope, $225 and $75 a paycheck (NEEDS) plus gas for any drive. Their charges come out of
# the envelope, not the wallet. Going over takes the overage from the wallet as it happens (the
# page asks him to okay it); a paycheck that comes in under asks, once it's over, whether to add
# what's left to the wallet. "Keep it saved" leaves it in his accounts. Per paycheck, not per
# month (his call, Nov 23).
NEEDS_FROM = date(2026, 11, 27)
NEEDS_MONTHLY = {n: b for n, (b, _) in NEEDS.items()}  # per paycheck, despite the name
NEEDS_PER_CHECK = round(sum(NEEDS_MONTHLY.values()), 2)  # 300.00


def is_need(row, name: str) -> bool:
    if name == "Groceries":
        return is_grocery(row)
    return is_gas_station(row) and abs(float(row["amount"])) >= GAS_MIN_FILLUP


def needs_month(d: date) -> tuple[date, date]:
    """The envelope period holding day d: its paycheck, [payday, next payday)."""
    return period_for(d)


def needs_spent(conn, name: str, a: date, b: date) -> float:
    return _cost([r for r in _rows(conn, a, b) if is_need(r, name)])


def needs_budget(conn, name: str, m0: date, m1: date, today: date) -> float:
    base = NEEDS_MONTHLY[name]
    if name == "Gas":
        base += sum(g for _, g in trip_gas(m0, m1, today, lambda a, b: needs_spent(conn, "Gas", a, b + timedelta(days=1))))
    return round(base, 2)


def _over(conn, name: str, m0: date, m1: date, upto: date, today: date) -> float:
    end = min(upto, m1)
    if end <= m0:
        return 0.0
    return max(needs_spent(conn, name, m0, end) - needs_budget(conn, name, m0, m1, today), 0.0)


def _cashins(conn, a: date, b: date) -> float:
    try:
        return float(conn.execute("SELECT COALESCE(SUM(amount), 0) FROM needs_cashins WHERE answer = 'add'"
                                  " AND answered_on >= ? AND answered_on < ?", (a.isoformat(), b.isoformat())).fetchone()[0])
    except Exception:  # an older database without the table
        return 0.0


def needs_effect(conn, a: date, b: date, today: date) -> dict | None:
    """What the envelope does to the wallet in paycheck [a, b): what it sets aside, the
    charges it pays instead of the wallet, the overage the wallet pays, and leftover added back."""
    if a < NEEDS_FROM:
        return None
    spent = sum(needs_spent(conn, n, a, b) for n in NEEDS_MONTHLY)
    over = 0.0
    for m0, m1 in {needs_month(a), needs_month(b - timedelta(days=1))}:
        for n in NEEDS_MONTHLY:
            over += _over(conn, n, m0, m1, b, today) - _over(conn, n, m0, m1, max(a, m0), today)
    # What comes off the top is this paycheck's budget: $300 plus gas for any drive in it.
    cut = sum(needs_budget(conn, n, a, a + timedelta(days=14), today) for n in NEEDS_MONTHLY)
    return {"set_aside": round(cut, 2), "spent": round(spent, 2), "over": round(over, 2),
            "cashin": round(_cashins(conn, a, b), 2)}


def envelope_balance(conn, at: date, today: date) -> float:
    """Groceries & gas money still sitting in his accounts on the morning of `at`."""
    bal, a = 0.0, NEEDS_FROM
    while a < at:
        e = needs_effect(conn, a, min(a + timedelta(days=14), at), today)
        bal += e["set_aside"] - e["spent"] + e["over"] - e["cashin"]
        a += timedelta(days=14)
    return round(bal, 2)


def needs_month_lines(conn, m0: date, m1: date, today: date) -> list[dict]:
    out = []
    for n in NEEDS_MONTHLY:
        budget = needs_budget(conn, n, m0, m1, today)
        spent = needs_spent(conn, n, m0, min(m1, today + timedelta(days=1)))
        out.append({"name": n, "budget": budget, "spent": round(spent, 2), "left": round(max(budget - spent, 0), 2),
                    "over": round(max(spent - budget, 0), 2)})
    return out


def leftover_for(conn, key: str, today: date) -> float | None:
    """What a finished paycheck's envelope (its payday, "YYYY-MM-DD") left unspent; None if it isn't over yet."""
    m0, m1 = needs_month(date.fromisoformat(key))
    if m1 > today or m0 < NEEDS_FROM:
        return None
    return round(sum(l["left"] for l in needs_month_lines(conn, m0, m1, today)), 2)


def needs_leftover(conn, today: date) -> dict | None:
    """The paycheck that just ended, with what its envelope left, until he answers the pop-up."""
    m0, _ = needs_month(today)
    p0, p1 = needs_month(m0 - timedelta(days=1))
    if p0 < NEEDS_FROM:
        return None
    key = p0.isoformat()
    try:
        if conn.execute("SELECT 1 FROM needs_cashins WHERE month = ?", (key,)).fetchone():
            return None
    except Exception:
        pass
    lines = needs_month_lines(conn, p0, p1, today)
    total = round(sum(l["left"] for l in lines), 2)
    if total < 1:
        return None
    return {"month": key, "label": f"{p0:%b} {p0.day} paycheck", "amount": total, "lines": lines}


# Charges he decided belong to a LATER paycheck than the one they posted in: the
# Oct 26 Costco run came out of the Oct 30 check, not the Oct 16 one. Hidden in
# the period it posted in and taken off the top of the period it belongs to.
# (date, merchant fragment, amount) -> the payday it should land on.
MOVED = [
    ((date(2026, 10, 27), "costco", 227.92), date(2026, 10, 30)),
]
MOVED_LABEL = "for the Oct 26 Costco run"

# What a paycheck's leftover (not bills, not spending money) is for, when he names it.
# Nov 13: kept as a cushion while he gets used to the first $699.33 loan autopay (Nov 23).
EXTRA_LABELS = {date(2026, 11, 13): "Cushion"}

# Charges the bank files as transfers that he says were really spending.
# (date, merchant fragment, amount). The Oct 13 Venmo to his brother Cristian was
# his share of the Florida trip (Nov 20).
COUNTED = [
    (date(2026, 10, 13), "venmo", 84.00),
    # Three doors from an Auction Nation doors auction (receipts in his email). The bank
    # files Auction Nation as automotive, which the bills rules skip. Denisse paid
    # $160 of it back ("doors", Oct 13), linked in owed.json.
    (date(2026, 10, 5), "auction nation", 12.55),
    (date(2026, 10, 7), "auction nation", 149.94),
    # His share of things Denisse paid for, sent to her by Zelle (Nov 20: "my amount is
    # spending of course"). Not her "aps" requests ($99.64 Oct 16, $43.15 Nov 13): those
    # are the electric bill. Not the $542 rent.
    (date(2026, 10, 5), "zelle payment to denisse", 9.43),
    (date(2026, 10, 19), "zelle payment to denisse", 9.50),
    (date(2026, 10, 19), "zelle payment to denisse", 25.00),
    (date(2026, 10, 30), "zelle payment to denisse", 10.00),
    (date(2026, 11, 13), "zelle payment to denisse", 11.28),
]

# Already paid for out of money he had set aside, so it must not count twice.
# The Acer monitor: cash from his monitor returns plus money put by beforehand.
COVERED = [
    # Litter at a pet store comes out of the Kitty budget line (Nov 22), not spending money.
    # D buys the food. Litter on Amazon or at Costco can't be told apart, so it still counts.
    *({"merchant": m, "low": 1.00, "high": 80.00, "after": date(2026, 11, 22), "before": date(2099, 1, 1)}
      for m in ("petsmart", "petco", "chewy")),
    # A monthly prescription copay, budgeted as a health bill (Nov 21), not spending money.
    # Exact amount only, so an ordinary grocery run there still counts.
    {"merchant": "safeway", "low": 24.66, "high": 24.68,
     "after": date(2026, 1, 1), "before": date(2099, 1, 1)},
    {"merchant": "micro center", "low": 395.00, "high": 425.00,
     "after": date(2026, 11, 7), "before": date(2026, 11, 22)},
    # Black Friday orders on Amazon, paid from the fund (Nov 20): the trimmer and socks
    # (charged as they ship) and a month of Prime. Any Amazon charge this size in this
    # window is taken as one of them.
    # Widened Nov 24 for the Black Friday clothes, board, heater and bins (~$223, maybe one
    # order, charged as items ship, possibly after Black Friday).
    {"merchant": "amazon", "low": 1.00, "high": 300.00, "after": date(2026, 11, 19), "before": date(2026, 12, 8)},
    {"merchant": "amzn", "low": 1.00, "high": 300.00, "after": date(2026, 11, 19), "before": date(2026, 12, 8)},
    # The Logitech mouse (Nov 26, $78.54), from the fund; a business expense.
    {"merchant": "logitech", "low": 76.00, "high": 81.00, "after": date(2026, 11, 25), "before": date(2026, 12, 20)},
    # MicroPerfumes order (Nov 20): the $2.96 left after the gift card.
    {"merchant": "microperfumes", "low": 2.50, "high": 3.50, "after": date(2026, 11, 19), "before": date(2026, 11, 30)},
    # The shaver, charged when it ships.
    {"merchant": "amazon", "low": 200.00, "high": 240.00, "after": date(2026, 12, 20), "before": date(2027, 1, 25)},
    {"merchant": "amzn", "low": 200.00, "high": 240.00, "after": date(2026, 12, 20), "before": date(2027, 1, 25)},
]

# Bills and money movement. None of this is spending money; it is handled before
# the number is worked out. Mirrors the exclusions on his Monarch page.
EXCLUDED_PRIMARY = {
    "LOAN_PAYMENTS",       # student loans and every card payment
    "RENT_AND_UTILITIES",  # rent, power, phone, internet
    "TRANSFER_IN",
    "TRANSFER_OUT",
    "BANK_FEES",
    "INCOME",
}
# Every one of these has its own line in his Monarch budget, so it is a bill with
# money already allocated, not flexible spending. Confirmed against the live
# November 2026 budget: Kitty Expenses $125 (the vet, shared with D),
# Therapy/Psych $50 (the quarterly prescription review), Fitness $25,
# Insurance $234, Auto Maintenance $100.
EXCLUDED_DETAILED = {
    "GENERAL_SERVICES_INSURANCE",
    "MEDICAL_VETERINARY_SERVICES",
    "MEDICAL_PRIMARY_CARE",
    "MEDICAL_DENTAL_CARE",
    "MEDICAL_EYE_CARE",
    "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS",
    "TRANSPORTATION_BIKES_AND_SCOOTERS",
    "GENERAL_SERVICES_AUTOMOTIVE",
}
# His parking spot, paid through Cash App, and his rent, paid by Zelle.
EXCLUDED_MERCHANT_LIKE = ("cash app", "zelle")

# Money a person sends back. Eddie's rule: "if there is a payback then it counts
# as money going back to the budget", so it is a credit against spending money
# rather than something to ignore. Only person-to-person app transfers qualify;
# moving his own money between his own accounts is matched as a transfer first
# and never reaches here, and his paycheck is INCOME, not a transfer.
PAYBACK_DETAILED = {"TRANSFER_IN_TRANSFER_IN_FROM_APPS"}

# Card rewards taken as a statement credit, and card benefit credits like the
# Known Traveler fee. Eddie confirmed on 2026-11-08 that these count: they are
# money back on spending that already came out of this budget, the same as a
# refund. Card PAYMENTS carry the identical Plaid category, so the two are told
# apart by name — anything reading like a payment is him moving his own money to
# the card, which is not money coming back.
CARD_CREDIT_DETAILED = "TRANSFER_IN_OTHER_TRANSFER_IN"
CARD_PAYMENT_LIKE = ("payment", "thank you", "autopay")

# What he can tell the app about a single transaction, overriding every rule
# below. A payback for something already budgeted (the vet, insurance) must NOT
# credit spending money, which is the whole reason PAYBACK_OTHER exists.
TAG_COUNTS = {"spending", "payback_spending"}
TAG_IGNORES = {"not_spending", "payback_other", "covered"}

# Subscriptions are bills, not spending money: his $1,700 already has $175 of them
# taken out, so counting them here would charge him twice. Plaid files them under
# no single category (Ring lands in HOME_IMPROVEMENT_SECURITY, Anthropic in
# GENERAL_SERVICES), so they are named explicitly.
#
# Only genuinely recurring charges belong here. A one-off purchase counts as
# spending money like anything else, even when the merchant looks subscription-like,
# so nothing goes on this list until it has actually billed month after month.
# His known subscriptions, confirmed 2026-11-11: Anthropic, Spotify, the Cobblestone car
# wash (every 4 weeks), Google One and Ring.
SUBSCRIPTION_MERCHANTS = ("ring", "anthropic", "spotify", "google one", "cobblestone")

# Places he does not tip: counter service, vending, water machines, apps.
NO_TIP = (
    "habit burger", "watermill", "nayax", "carl's jr", "dunkin",
    "taco bell", "domino", "someburros", "mcdonald", "burger king", "wendy",
    "jack in the box", "in-n-out", "chick-fil-a", "chipotle", "panda express",
    "subway", "sonic", "starbucks", "dutch bros", "del taco", "wingstop",
    "raising cane", "five guys", "little caesars", "pizza hut", "papa john",
    "anepalco",  # breakfast spot the bank files as a salon; he confirmed no tip
    "beyond",  # online grocery order
)
# Charges worth asking "was this split?" about, from what he told us on 2026-11-16:
# he rarely spends over $15 alone at fast food, a sit-down meal of $30+ is often for
# two, and he isn't drinking for a while, so any bar tab is worth a question.
# Detailed category -> the smallest amount (tip estimate included) that asks.
SPLIT_ASK = {
    "FOOD_AND_DRINK_FAST_FOOD": 15.01,
    "FOOD_AND_DRINK_RESTAURANT": 30.00,
    "FOOD_AND_DRINK_BEER_WINE_AND_LIQUOR": 10.00,
}
SPLIT_ASK_FROM = date(2026, 11, 16)  # the sushi, ramen and breakfast before this are settled


def split_question(row) -> str | None:
    """Why this charge looks shared, or None if it doesn't (or he already answered)."""
    if row.get("answer") or row.get("tag") or float(row["amount"]) >= 0 or not _is_spending(row):
        return None
    low = SPLIT_ASK.get(row["pfc_detailed"] or "")
    if low is None or -float(row["amount"]) + _tip(row) < low:
        return None
    return {"FOOD_AND_DRINK_FAST_FOOD": "More than you usually spend alone at fast food",
            "FOOD_AND_DRINK_BEER_WINE_AND_LIQUOR": "A bar tab",
            }.get(row["pfc_detailed"], "A sit-down meal over $30")


# Pending charges here may still gain a tip before they post.
TIP_PRIMARY = {"FOOD_AND_DRINK", "ENTERTAINMENT"}
TIP_ALWAYS_DETAILED = {"PERSONAL_CARE_HAIR_AND_BEAUTY"}


def period_for(day: date) -> tuple[date, date]:
    """The fortnight containing `day`: (payday it started on, next payday)."""
    k = (day - PAY_ANCHOR).days // 14
    start = PAY_ANCHOR + timedelta(days=k * 14)
    return start, start + timedelta(days=14)


def _moved_to(row) -> date | None:
    """The payday a charge was reassigned to, if Eddie moved it; otherwise None."""
    for (when, merchant, amount), payday in MOVED:
        if (when.isoformat() in (row["date"], row.get("posted"))
                and merchant in (row["what"] or "").lower()
                and abs(-float(row["amount"]) - amount) < 0.01):
            return payday
    return None


# Charges (and the paybacks tied to them) he said are a bill, not spending money (his note
# on the charge, Nov 23), with the payback that went with it.
NOT_SPENDING = [
    (date(2026, 11, 19), "safeway", 48.22),
    (date(2026, 11, 20), "zelle payment from denisse", 18.26),
]


# A subscription on Chase Sapphire (..9203), matched by card, amount and date so its name stays
# out of this public code. A bill, never spending money. Upgraded Nov 24: $369.60 + tax on Nov 29,
# then $422 + tax every 180 days (it was ~$244 every 90).
PRIVATE_SUBS = [
    {"what": "6-month subscription", "card": "9203", "due": date(2026, 11, 29), "amount": 403.00, "lo": 390, "hi": 415},
    {"what": "6-month subscription", "card": "9203", "due": date(2027, 5, 28), "amount": 460.40, "lo": 440, "hi": 475},
]


def _is_private_sub(row) -> bool:
    if row.get("mask") is None or float(row["amount"]) >= 0:
        return False
    d = date.fromisoformat(row["date"])
    return any(row["mask"] == x["card"] and x["lo"] <= -float(row["amount"]) <= x["hi"]
               and abs((d - x["due"]).days) <= 5 for x in PRIVATE_SUBS)


def _is_not_spending(row) -> bool:
    if _is_private_sub(row):
        return True
    return any(when.isoformat() in (row["date"], row.get("posted"))
               and merchant in ((row["what"] or "") + " " + (row.get("raw_name") or "")).lower()
               and abs(abs(float(row["amount"])) - amount) < 0.01
               for when, merchant, amount in NOT_SPENDING)


def _is_counted(row) -> bool:
    """A transfer he said was really spending (COUNTED)."""
    return any(when.isoformat() in (row["date"], row.get("posted"))
               and merchant in (row["what"] or "").lower()
               and abs(-float(row["amount"]) - amount) < 0.01
               for when, merchant, amount in COUNTED)


def _is_covered(row) -> bool:
    """Paid with money set aside for it, so it is not spending money."""
    name = (row["what"] or "").lower()
    amount = -float(row["amount"])
    for c in COVERED:
        if (c["merchant"] in name
                and c["low"] <= amount <= c["high"]
                and c["after"].isoformat() <= row["date"] <= c["before"].isoformat()):
            return True
    return False


def _is_card_credit(row) -> bool:
    """Cash back or a benefit credit the card gave back, not a payment he made."""
    if float(row["amount"]) <= 0 or row["is_transfer"]:
        return False
    acct = (row["account_type"] if "account_type" in row.keys() else None) or ""
    if acct != "credit":
        return False
    if (row["pfc_detailed"] or "") != CARD_CREDIT_DETAILED:
        return False
    return not any(x in (row["what"] or "").lower() for x in CARD_PAYMENT_LIKE)


def _is_payback(row) -> bool:
    """
    Money a person sent back, e.g. a Zelle repayment for a shared bill.

    The transfer matcher marks these `is_transfer` with kind 'reimbursement',
    which is right for keeping them out of income but wrong here: Eddie's rule is
    that a payback goes back into the budget, so it has to be looked at before
    the is_transfer check rather than swallowed by it. A real internal transfer
    has kind 'account_transfer' or 'card_payment' and is still ignored.
    """
    if float(row["amount"]) <= 0 or _is_needs_refund(row):
        return False
    kind = row["transfer_kind"] if "transfer_kind" in row.keys() else None
    if kind == "reimbursement":
        return True
    if _is_card_credit(row):
        return True
    if row["is_transfer"]:
        return False
    return (row["pfc_detailed"] or "") in PAYBACK_DETAILED


def _is_spending(row) -> bool:
    # What Eddie said about this exact transaction beats anything inferred.
    tag = row["tag"] if "tag" in row.keys() else None
    if tag in TAG_COUNTS:
        return True
    if tag in TAG_IGNORES:
        return False
    if _is_needs_refund(row):
        return True  # a refund that lowers what groceries cost
    if _is_not_spending(row):
        return False
    # Before the is_transfer and name rules below, either of which would drop it.
    if _is_payback(row):
        return True
    if _is_counted(row):
        return True
    if row["is_transfer"]:
        return False
    if _moved_to(row) or _is_covered(row):
        return False
    if (row["pfc_primary"] or "") in EXCLUDED_PRIMARY:
        return False
    if (row["pfc_detailed"] or "") in EXCLUDED_DETAILED:
        return False
    name = (row["what"] or "").lower()
    if any(x in name for x in EXCLUDED_MERCHANT_LIKE):
        return False
    if any(x in name for x in SUBSCRIPTION_MERCHANTS):
        return False
    return True


def _tip(row) -> float:
    """A pending restaurant or bar charge usually posts before the tip is added."""
    amount = -float(row["amount"])
    if not row["pending"] or amount <= 0 or is_gas_station(dict(row)):
        return 0.0
    name = (row["what"] or "").lower()
    if any(x in name for x in NO_TIP):
        return 0.0
    detailed = row["pfc_detailed"] or ""
    primary = row["pfc_primary"] or ""
    if detailed == "FOOD_AND_DRINK_GROCERIES" or is_grocery(dict(row)):  # nobody tips at Safeway
        return 0.0
    if detailed in TIP_ALWAYS_DETAILED:
        return amount * TIP_RATE
    if primary in TIP_PRIMARY and amount >= TIP_MIN:
        return amount * TIP_RATE
    return 0.0


def _expected(conn, start: date, end: date) -> list[dict]:
    out = []
    for e in EXPECTED:
        if not start <= e["date"] < end:
            continue
        landed = conn.execute(
            "SELECT 1 FROM transactions t JOIN accounts a ON a.id = t.account_id"
            " WHERE a.mask = ? AND ABS(t.amount + ?) < ? AND t.date BETWEEN ? AND ?"
            " AND LOWER(COALESCE(t.merchant_name, '') || t.name) LIKE ?",
            (e["mask"], e["amount"], e.get("tol", 0.01), e["date"].isoformat(),
             (e["date"] + timedelta(days=7)).isoformat(), f'%{e.get("like", "")}%')).fetchone()
        if not landed:
            out.append({"amount": -e["amount"], "date": e["date"].isoformat(), "posted": None, "pending": 1,
                        "is_transfer": 0, "pfc_primary": e["pfc_primary"], "pfc_detailed": e["pfc_detailed"],
                        "transfer_kind": None, "what": e["what"], "raw_name": e["what"], "tag": None,
                        "account_type": "credit", "pid": e["id"], "mask": e["mask"], "answer": None, "answer_who": None,
                        "answered_on": None})
    return out


def _rows(conn, start: date, end: date) -> list[dict]:
    return _expected(conn, start, end) + [
        _real_amount(dict(r))
        for r in conn.execute(
            "SELECT t.amount, COALESCE(t.authorized_date, t.date) AS date, t.date AS posted, t.pending, t.is_transfer, t.pfc_primary,"
            " t.pfc_detailed, t.transfer_kind, COALESCE(t.merchant_name, t.name) AS what, t.name AS raw_name, COALESCE(t.authorized_at, t.created_at) AS seen,"
            " n.tag AS tag, a.type AS account_type, a.mask AS mask,"
            " t.plaid_transaction_id AS pid, ans.answer AS answer, ans.who AS answer_who, ans.answered_on AS answered_on"
            " FROM transactions t"
            " JOIN accounts a ON a.id = t.account_id"
            " LEFT JOIN transaction_notes n ON n.transaction_id = t.id"
            " LEFT JOIN answers ans ON ans.plaid_transaction_id = t.plaid_transaction_id"
            " WHERE COALESCE(t.authorized_date, t.date) >= ? AND COALESCE(t.authorized_date, t.date) < ?",
            (start.isoformat(), end.isoformat()),
        )
    ]


def _counts_as_payback(row) -> bool:
    """Money coming back that belongs to spending money, by his say-so or by default."""
    tag = row["tag"] if "tag" in row.keys() else None
    if tag == "payback_spending":
        return True
    if tag is not None:
        return False  # he put it somewhere else on purpose
    return _is_payback(row)


def _cost(rows: list[dict]) -> float:
    """
    What a set of charges costs, tip estimate included. Refunds reduce it.
    Paybacks are kept out and reported on their own line, so the page can show
    the money going out and the money coming back rather than one net figure.
    """
    total = 0.0
    for r in rows:
        if not _is_spending(r) or _counts_as_payback(r):
            continue
        total += -float(r["amount"]) + _tip(r)
    return total


def _paid_back(rows: list[dict]) -> float:
    """Money people sent back that goes straight back into spending money."""
    return sum(
        float(r["amount"]) for r in rows if _is_spending(r) and _counts_as_payback(r)
    )


CHAIN_FROM = date(2026, 11, 13)


def overspend_detail(rows: list[dict], allowance: float, env: dict | None, start: date) -> dict:
    """Which charges took a paycheck below zero, in the order they happened: walking its spending
    from what it had, the ones after the money ran out (and the part of the one that crossed)."""
    running, items, back_after = allowance, [], 0.0
    for r in sorted(rows, key=lambda r: (r["date"], str(r.get("seen") or ""))):
        if not _is_spending(r):
            continue
        if env and any(is_need(r, n) for n in NEEDS_MONTHLY):
            continue  # paid from the groceries & gas envelope; only its overage counts, added below
        if _counts_as_payback(r):
            before, running = running, running + float(r["amount"])
            if before < 0:
                back_after += min(float(r["amount"]), -before)
            continue
        cost = -float(r["amount"]) + _tip(r)
        before, running = running, running - cost
        if cost < 0 and before < 0:  # a refund once it was already over
            back_after += min(-cost, -before)
        if running < 0 and cost > 0:
            items.append({"date": r["date"], "what": r["what"], "amount": round(cost, 2),
                          "over": round(min(cost, -running) if before > 0 else cost, 2)})
    if env and env["over"] > 0.005:
        items.append({"date": None, "what": "Groceries & gas over budget", "amount": env["over"], "over": env["over"]})
    return {"payday": start.isoformat(), "items": items, "paid_back_after": round(back_after, 2)}


def summary(conn, today: date | None = None) -> dict:
    today = today or date.today()
    start, nxt = period_for(today)
    prev_start = start - timedelta(days=14)

    set_aside = SET_ASIDE.get(start, 0.0)
    prev_set = SET_ASIDE.get(prev_start, 0.0)
    moved_in = sum(a for (_, _, a), payday in MOVED if payday == start)
    prev_moved_in = sum(a for (_, _, a), payday in MOVED if payday == prev_start)
    route_now = routed(conn, start)
    route_prev = routed(conn, prev_start)

    # Carry over: from CHAIN_FROM on, the last paycheck's whole result carries in, so an overspend
    # comes out of the next paycheck in full and never drops off (his rule, Nov 24). Before that,
    # one paycheck of carry, as his old page did it.
    prev_rows = _rows(conn, prev_start, start)
    prev_env = needs_effect(conn, prev_start, start, today)
    if prev_start >= CHAIN_FROM:
        prev = summary(conn, prev_start)
        roll, prev_allow = prev["left"], prev["allowance"]
    else:
        prev_cap = PER_PERIOD - prev_set - prev_moved_in - sum(r["amount"] for r in route_prev)
        roll = prev_cap - _cost(prev_rows) + _paid_back(prev_rows)
        prev_allow = prev_cap
        if prev_env:
            roll += -prev_env["set_aside"] + prev_env["spent"] - prev_env["over"] + prev_env["cashin"]
            prev_allow -= prev_env["set_aside"]

    fund_back = round(fund_left(FUND_CLOSE), 2) if start == FUND_CLOSE else 0.0
    cap = PER_PERIOD - set_aside - moved_in + roll - sum(r["amount"] for r in route_now) + fund_back
    rows = _rows(conn, start, nxt)
    spent = _cost(rows)
    paid_back = _paid_back(rows)
    env = needs_effect(conn, start, nxt, today)
    if env:  # groceries & gas come off the top; only going over touches the wallet
        cap -= env["set_aside"]
        spent += -env["spent"] + env["over"]
        paid_back += env["cashin"]
    left = cap - spent + paid_back

    tips = sum(_tip(r) for r in rows if _is_spending(r))
    return {
        "monthly_budget": MONTHLY_BUDGET,
        "per_period": round(PER_PERIOD, 2),
        "period_start": start.isoformat(),
        "next_payday": nxt.isoformat(),
        "days_to_payday": (nxt - today).days,
        "set_aside": round(set_aside, 2),
        "set_aside_label": SET_ASIDE_LABEL,
        "moved_in": round(moved_in, 2),
        "moved_in_label": MOVED_LABEL,
        "extra_label": EXTRA_LABELS.get(start, "Extra savings"),
        "rolled_over": round(roll, 2),
        "fund_back": fund_back,
        "overspend": overspend_detail(prev_rows, prev_allow, prev_env, prev_start) if roll < -0.5 else None,
        "allowance": round(cap, 2),
        "spent": round(spent, 2),
        "paid_back": round(paid_back, 2),
        "left": round(left, 2),
        "tip_estimate": round(tips, 2),
        "needs_env": env,
        "freed": FREED["amount"] if start >= FREED["from"] and not FREED["home"] and ended_open(conn) else 0.0,
        "routed": [{"what": r["what"], "amount": r["amount"], "from": r["from_name"]} for r in route_now],
    }


# ---- Monthly budget -------------------------------------------------------------------
# His month-by-month plan (Nov 21, 2026): every month allots money to each bill, and the
# two months a year with a third paycheck are the bonus. Amounts are his Monarch lines,
# checked against his accounts on Nov 21 (Aidvantage, State Farm, Verizon, Plaid, Gmail).
# `from`/`until` bound a line to months ("YYYY-MM", inclusive). `match` (a regex on the
# merchant, or `cats` on Plaid's detailed category) finds what actually went out that
# month; a line without one is shown as planned only. `paycheck` lines come out before
# the deposit lands, so they're listed but not taken off take-home pay.
PAYCHECK = 1986.71  # usual take-home deposit (some checks run up to $2,010.65)
BUDGET = [
    {"what": "Rent", "group": "Home", "amount": 542.00, "match": r"zelle payment to denisse", "lo": 541, "hi": 543},
    {"what": "Gas & electric (your half)", "group": "Home", "amount": 110.00, "note": "varies, Denisse sends the request"},
    {"what": "Phone (Verizon)", "group": "Home", "amount": 101.90, "match": r"verizon", "lo": 80, "hi": 140},
    # November at the $327.26 minimum (Aidvantage autopay confirmed Nov 26); back to $699.33 from December
    # until he sets the ramp-up amount.
    {"what": "Student loan", "group": "Loans", "amount": 327.26, "from": "2026-11", "until": "2026-11", "match": r"aidvantage|dept ?of ?ed", "lo": 300, "hi": 760},
    {"what": "Student loan", "group": "Loans", "amount": 699.33, "from": "2026-12", "match": r"aidvantage|dept ?of ?ed", "lo": 650, "hi": 760},
    {"what": "Car insurance (State Farm)", "group": "Car", "amount": 253.80, "until": "2026-12", "match": r"state farm", "lo": 200, "hi": 270},
    # GEICO's $1,257.50 (Dec 26) goes on a card that savings pays off, so it never hits a month;
    # the budget pays savings back instead (Eddie, Nov 21).
    {"what": "Car insurance (pay savings back for GEICO)", "group": "Car", "amount": round(1257.50 / 6, 2), "from": "2027-01",
     "note": "savings paid the 6-month policy; this pays it back"},
    {"what": "Car maintenance", "group": "Car", "amount": 100.00, "note": "set aside for repairs"},
    {"what": "Parking", "group": "Car", "amount": 30.00, "match": r"cash app\*gee", "lo": 1, "hi": 35},
    # Kitty is monthly: his half of the monthly shot ($124.50) plus litter (~$23). Checkups are
    # set aside: ~$250 a year, his half. The $500 fight visit this year was a one-off (Eddie, Nov 22).
    {"what": "Kitty (monthly shot, litter)", "group": "Health", "amount": round(124.50 / 2 + 23, 2), "cats": {"MEDICAL_VETERINARY_SERVICES"},
     "match": r"petsmart|petco|chewy", "lo": 1, "hi": 80, "note": "your half of the shot; D sends the other half"},
    {"what": "Vet checkups (set aside)", "group": "Health", "amount": round(250 / 2 / 12, 2), "note": "about $250 a year, your half"},
    {"what": "Therapy (set aside)", "group": "Health", "amount": 50.00, "cats": {"MEDICAL_PRIMARY_CARE"}, "note": "$150 a quarter"},
    {"what": "Prescription", "group": "Health", "amount": 24.67, "match": r"safeway", "lo": 24.66, "hi": 24.68,
     "note": "monthly copay"},
    {"what": "Fitness", "group": "Health", "amount": 25.00, "paycheck": True, "note": "comes out of your paycheck"},
    # Small costs that come once a year or every few months, grouped (his call, Nov 21):
    # car registration $76.48 (Jun 30), lab work ~$200 (the $371 in Oct 2026 covered three years), a refill ~$30 every 3 months, Costco share ~$30.
    {"what": "Small yearly stuff", "group": "Yearly", "amount": round((76.48 + 200 + 120 + 30) / 12, 2),
     "note": "about $426 a year: car registration, lab work, a refill, Costco share"},
    {"what": "Anthropic", "group": "Subscriptions", "amount": 109.10, "match": r"anthropic", "lo": 15, "hi": 250},
    {"what": "Spotify", "group": "Subscriptions", "amount": 14.17, "match": r"spotify", "lo": 5, "hi": 30},
    {"what": "Ring", "group": "Subscriptions", "amount": 21.80, "match": r"^ring\b", "lo": 5, "hi": 25, "note": "two plans, both yours"},
    {"what": "Car wash", "group": "Subscriptions", "amount": round(21.00 * 13 / 12, 2), "match": r"cobblestone", "lo": 15, "hi": 30, "note": "every 4 weeks"},
    {"what": "6-month subscription", "group": "Subscriptions", "amount": round(460.40 * 365 / 180 / 12, 2), "from": "2026-11",
     "note": "about $403 on Nov 29, then about $460 with tax every 180 days (next late May); on Chase Sapphire"},
    {"what": "Google One", "group": "Subscriptions", "amount": 19.99, "from": "2027-02", "match": r"google", "lo": 5, "hi": 30, "note": "$19.99 a month once the free trial ends Feb 13"},
]


def paydays_in(year: int, month: int) -> list[date]:
    first = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    d, _ = period_for(first)
    if d < first:
        d += timedelta(days=14)
    out = []
    while d < nxt:
        out.append(d)
        d += timedelta(days=14)
    return out


def _month_lines(key: str) -> list[dict]:
    return [b for b in BUDGET if b.get("from", "0000-00") <= key <= b.get("until", "9999-99")]


def monthly_budget(conn, today: date, months: int = 12) -> dict:
    """This month with what actually went out, then the months after it as planned."""
    out = []
    y, m = today.year, today.month
    for i in range(months):
        key = f"{y:04d}-{m:02d}"
        first = date(y, m, 1)
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        pays = paydays_in(y, m)
        lines = []
        rows = None
        if i == 0:  # only this month has actuals worth showing
            rows = [dict(r) for r in conn.execute(
                "SELECT -t.amount AS amt, COALESCE(t.merchant_name, '') || ' | ' || t.name AS what, t.pfc_detailed AS cat"
                " FROM transactions t WHERE t.amount < 0 AND COALESCE(t.authorized_date, t.date) >= ?"
                " AND COALESCE(t.authorized_date, t.date) < ?", (first.isoformat(), nxt.isoformat()))]
        for b in _month_lines(key):
            actual = None
            if rows is not None and ("match" in b or "cats" in b):
                hits = [r["amt"] for r in rows if r["cat"] in b.get("cats", ())
                        or ("match" in b and b["lo"] <= r["amt"] <= b["hi"]
                            and any(re.search(b["match"], p.strip(), re.I) for p in r["what"].split("|")))]
                actual = round(sum(hits), 2)
            lines.append({"what": b["what"], "group": b["group"], "amount": b["amount"], "actual": actual,
                          "paycheck": bool(b.get("paycheck")), "note": b.get("note", "")})
        income = round(len(pays) * PAYCHECK, 2)
        bills = round(sum(l["amount"] for l in lines if not l["paycheck"]), 2)
        spending = round(len(pays) * PER_PERIOD, 2)
        month = {"key": key, "label": first.strftime("%B %Y"), "paydays": [p.isoformat() for p in pays],
                 "bonus": len(pays) == 3, "income": income, "bills": bills, "spending": spending,
                 "left": round(income - bills - spending, 2), "lines": lines}
        if i == 0:
            got = conn.execute(
                "SELECT COALESCE(SUM(t.amount), 0) FROM transactions t JOIN accounts a ON a.id = t.account_id"
                " WHERE a.mask = ? AND t.amount > 0 AND t.pfc_detailed = 'INCOME_SALARY' AND t.date >= ? AND t.date < ?",
                (BILLS_ACCOUNT, first.isoformat(), nxt.isoformat())).fetchone()[0]
            spent_rows = _rows(conn, first, min(nxt, today + timedelta(days=1)))
            month["income_actual"] = round(float(got), 2)
            month["spending_actual"] = round(_cost(spent_rows) - _paid_back(spent_rows), 2)
        out.append(month)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return {"paycheck": PAYCHECK, "per_check": round(PER_PERIOD, 2), "months": out}


# ---- Money moves (Eddie, Nov 22) -------------------------------------------------------
# He moves round amounts out of Chase checking himself. The first paycheck of each month
# puts rent into Chase Savings, and he moves it back before rent goes out; the loan comes
# straight out of checking after the second paycheck (Eddie, Nov 22). Every paycheck sends
# the quarterly and yearly money to Ally Savings, and when one of those bills lands on a
# card he pulls that amount back. The app reminds him; it never moves money.
MOVES_FROM = date(2026, 11, 27)   # first payday under this plan
TO_CHASE_SAVINGS = 550.00          # rent is $542; first paycheck of the month only
TO_ALLY = 250.00                   # the quarterly and yearly bills are about $244 a paycheck, rounded up
# The student loan moves to Ally (his plan, Nov 24): $350 a paycheck rides along to Ally Savings,
# a recurring Ally transfer moves $699.33 to Ally Spending before the 28th, and Aidvantage
# autopays from Ally Spending. The Nov 28 payment still comes out of Chase checking.
LOAN_TO_ALLY = 350.00
LOAN_ALLY_FROM = date(2026, 12, 11)  # first paycheck that sends the loan money to Ally


def to_ally(payday: date) -> float:
    return TO_ALLY + (LOAN_TO_ALLY if payday >= LOAN_ALLY_FROM else 0.0)
CHASE_SAVINGS, ALLY_SPENDING, ALLY_SAVINGS = "6725", "2628", "2617"
SAVED_BILLS = {"Rent": 542.00}  # bill -> amount to move back; the loan autopays from checking
SAVED_FROM = date(2026, 12, 1)     # bills due before this were paid from checking as before
MOVE_BACK_DAYS = 6                 # start asking this many days before the bill is due
# Quarterly and yearly bills that land on a card and come back out of Ally.
FROM_ALLY = [
    {"what": "Car insurance", "cats": {"GENERAL_SERVICES_INSURANCE"}},
    {"what": "Therapy", "cats": {"MEDICAL_PRIMARY_CARE"}},
    {"what": "Vet visit", "cats": {"MEDICAL_VETERINARY_SERVICES"}, "lo": 130.00},  # not the monthly shot
    {"what": "Lab work", "match": r"sonora quest|labcorp|quest diag"},
    {"what": "Car registration", "match": r"az mvd|mvd fee|servicearizona"},
]
FROM_ALLY_SINCE = date(2026, 11, 22)
# Big ones on a card the app can't see yet (the new card isn't linked): date, what, amount.
FROM_ALLY_KNOWN = [(date(2026, 12, 26), "GEICO, 6 months, on the new card", 1257.50, r"geico")]


def _tx(conn, mask: str, a: date, b: date):
    return conn.execute(
        "SELECT t.amount, LOWER(COALESCE(t.merchant_name, '') || ' | ' || t.name) AS what FROM transactions t"
        " JOIN accounts acc ON acc.id = t.account_id WHERE acc.mask = ?"
        " AND COALESCE(t.authorized_date, t.date) BETWEEN ? AND ?", (mask, a.isoformat(), b.isoformat())).fetchall()


def _moved(conn, mask: str, pattern: str, lo: float, a: date, b: date, sign: int) -> bool:
    """Whether account `mask` shows a matching transfer of at least `lo` between a and b.
    sign -1 looks for money leaving the account, +1 for money arriving."""
    return any(float(r["amount"]) * sign >= lo and re.search(pattern, r["what"]) for r in _tx(conn, mask, a, b))


def _first_check(payday: date) -> bool:
    return paydays_in(payday.year, payday.month)[:1] == [payday]


def _bonus_check(payday: date) -> bool:
    """The third paycheck in a month: the bills are already covered by the other two."""
    return payday in paydays_in(payday.year, payday.month)[2:]


def money_moves(conn, today: date) -> list[dict]:
    """What he should move by hand right now, oldest first. Each is done once the bank shows it."""
    start, nxt = period_for(today)
    moves = []
    if start >= MOVES_FROM and not _bonus_check(start):
        a, b = start - timedelta(days=1), nxt - timedelta(days=1)
        if _first_check(start):
            moves.append({"id": f"pay-{start}-chase", "kind": "payday", "bank": "chase", "date": start.isoformat(),
                          "what": "Rent", "amount": TO_CHASE_SAVINGS,
                          "from": "Chase checking ··7372", "to": "Chase Savings ··6725",
                          "how": "Chase → Pay & transfer → Transfer between your accounts. It's instant.",
                          "done": _moved(conn, BILLS_ACCOUNT, r"to sav|6725", TO_CHASE_SAVINGS - 1, a, b, -1)})
        ally = to_ally(start)
        moves.append({"id": f"pay-{start}-ally", "kind": "payday", "bank": "ally", "date": start.isoformat(),
                      "what": "Quarterly and yearly bills" + (" + student loan" if ally > TO_ALLY else ""), "amount": ally,
                      "from": "Chase checking ··7372", "to": "Ally Savings ··2617",
                      "how": "Ally → Transfers → from your Chase checking into Ally Savings. Takes 1–3 days.",
                      "done": _moved(conn, BILLS_ACCOUNT, r"ally", ally - 1, a, b, -1)
                              or _moved(conn, ALLY_SAVINGS, r"jpmorgan|chase", ally - 1, a, b, 1)
                              or _moved(conn, ALLY_SPENDING, r"jpmorgan|chase", ally - 1, a, b, 1)})
    for r in routed(conn, start):
        sent = any(abs(-float(t["amount"]) - r["amount"]) < 1 and re.search(r["pattern"], t["what"])
                   for t in _tx(conn, BILLS_ACCOUNT, start - timedelta(days=1), nxt))
        moves.append({"id": f"route-{r['id']}-{start}", "kind": "fund", "bank": "chase", "date": start.isoformat(),
                      "what": f"{r['what']} (was {r['from_name']})", "amount": r["amount"],
                      "from": "Chase checking ··7372", "to": r["to"],
                      "how": "Chase → Pay & transfer. Moves to Chase Savings are instant; to Ally take 1–3 days.",
                      "done": sent})
    back = round(fund_left(FUND_CLOSE), 2)
    if start == FUND_CLOSE and back > 0:
        moves.append({"id": f"fund-close-{FUND_CLOSE}", "kind": "fund", "bank": "chase", "date": FUND_CLOSE.isoformat(),
                      "what": "Black Friday Fund leftover back to spending money", "amount": back,
                      "from": "Chase Savings ··6725", "to": "Chase checking ··7372",
                      "how": "Chase → Pay & transfer → Transfer between your accounts. It's instant.",
                      "done": _moved(conn, BILLS_ACCOUNT, r"from sav|6725", back - 1, FUND_CLOSE - timedelta(days=1), nxt, 1)})
    for d, x in FUND_REFILLS:
        if start <= d < nxt:
            moves.append({"id": f"fund-{d}", "kind": "fund", "bank": "chase", "date": d.isoformat(),
                          "what": "Black Friday Fund refill", "amount": x,
                          "from": "Chase checking ··7372", "to": "Chase Savings ··6725",
                          "how": "Chase → Pay & transfer → Transfer between your accounts. It's instant.",
                          "done": _moved(conn, BILLS_ACCOUNT, r"to sav|6725", x - 1, d - timedelta(days=1), nxt, -1)})
    # Before rent and the loan come out of checking, their money comes back from Chase Savings.
    for bill in BILLS:
        back = SAVED_BILLS.get(bill["what"])
        if not back:
            continue
        for due in _due_dates(bill, today - timedelta(days=5), today + timedelta(days=MOVE_BACK_DAYS + 1)):
            if due < SAVED_FROM:
                continue
            w0, w1 = due - timedelta(days=12), due + timedelta(days=5)
            if any(bill["lo"] <= -float(r["amount"]) <= bill["hi"] and re.search(bill["match"], r["what"])
                   for r in _tx(conn, BILLS_ACCOUNT, w0, w1)):
                continue  # already paid
            moves.append({"id": f"back-{bill['what']}-{due}", "kind": "back", "bank": "chase", "date": due.isoformat(),
                          "what": bill["what"].replace(" autopay", "") + f", due {due:%b} {due.day}", "amount": back,
                          "from": "Chase Savings ··6725", "to": "Chase checking ··7372",
                          "how": "Chase → Pay & transfer → Transfer between your accounts. It's instant.",
                          "done": _moved(conn, BILLS_ACCOUNT, r"from sav|6725", back - 1, due - timedelta(days=10), today, 1)})
    # No reminder to pay checking back for envelope buys: Chase checking, Chase Savings and Ally
    # Spending are one pool to him (Nov 23), so the wallet just counts that money as owed back.
    # Quarterly and yearly bills that landed on a card: that much comes back from Ally.
    hits = []
    for r in conn.execute(
            "SELECT -t.amount AS amt, COALESCE(t.authorized_date, t.date) AS day, t.pfc_detailed AS cat,"
            " COALESCE(t.merchant_name, t.name) AS what FROM transactions t JOIN accounts acc ON acc.id = t.account_id"
            " WHERE acc.type = 'credit' AND t.amount < 0 AND t.pending = 0 AND COALESCE(t.authorized_date, t.date) >= ?"
            " ORDER BY day", (FROM_ALLY_SINCE.isoformat(),)):
        for f in FROM_ALLY:
            ok = (r["cat"] in f.get("cats", ())) or ("match" in f and re.search(f["match"], (r["what"] or "").lower()))
            if ok and float(r["amt"]) >= f.get("lo", 0):
                hits.append((r["day"], f["what"], round(float(r["amt"]), 2)))
                break
    for when, what, amt, pat in FROM_ALLY_KNOWN:
        seen = any(re.search(pat, (r["what"] or "").lower()) for r in conn.execute(
            "SELECT COALESCE(t.merchant_name, t.name) AS what FROM transactions t WHERE t.amount < 0"
            " AND COALESCE(t.authorized_date, t.date) BETWEEN ? AND ?",
            ((when - timedelta(days=30)).isoformat(), (when + timedelta(days=10)).isoformat())))
        if not seen and today >= when - timedelta(days=MOVE_BACK_DAYS):
            hits.append((when.isoformat(), what, amt))
    hits.sort()
    pulled = 0.0
    for mask in (ALLY_SPENDING, ALLY_SAVINGS):
        pulled += sum(-float(r["amount"]) for r in _tx(conn, mask, FROM_ALLY_SINCE, today + timedelta(days=1))
                      if float(r["amount"]) < 0 and re.search(r"jpmorgan|chase", r["what"]) and "6725" not in r["what"])
    for day_, what, amt in hits:  # oldest first, each paid back in turn
        if pulled >= amt - 0.5:
            pulled -= amt
            continue
        d = date.fromisoformat(day_)
        moves.append({"id": f"ally-{day_}-{what}", "kind": "ally", "bank": "ally", "date": day_,
                      "what": f"{what}, {'charged' if d <= today else 'due'} {d:%b} {d.day}", "amount": round(amt - pulled, 2),
                      "from": "Ally Savings ··2617", "to": "Chase checking ··7372",
                      "how": "Ally → Transfers → Savings to your Chase checking. Takes 1–3 days, so do it before the card is due.",
                      "done": False})
        pulled = 0.0
    # No interest sweeps: he only moves money to put it aside for a bill, like rent (Nov 23).
    return moves


# ---- Money calendar (his ask, Nov 24) ----------------------------------------------------
# Paydays, bills, money moves and drives by date, for the page's calendar.
def _paid_near(conn, bill: dict, due: date) -> bool:
    for r in conn.execute("SELECT -t.amount AS amt, COALESCE(t.merchant_name, '') || ' | ' || t.name AS what FROM transactions t"
                          " WHERE t.amount < 0 AND COALESCE(t.authorized_date, t.date) BETWEEN ? AND ?",
                          ((due - timedelta(days=12)).isoformat(), (due + timedelta(days=5)).isoformat())):
        if bill.get("each_lo", bill["lo"]) <= r["amt"] <= bill.get("each_hi", bill["hi"]) and                 any(re.search(bill["match"], part.strip().lower()) for part in r["what"].split("|")):
            return True
    return False


def money_calendar(conn, today: date, back: int = 21, ahead: int = 90) -> list[dict]:
    a, b = today - timedelta(days=back), today + timedelta(days=ahead)
    out = []
    d, _ = period_for(a)
    while d < b:  # paydays
        if d >= a:
            out.append({"date": d.isoformat(), "what": "Payday" + (" (bonus check)" if _bonus_check(d) else ""),
                        "amount": PAYCHECK, "kind": "payday", "done": d <= today})
            if d >= MOVES_FROM:
                for m in money_moves(conn, d):
                    if m["kind"] in ("payday", "fund"):
                        out.append({"date": d.isoformat(), "what": f'Move: {m["what"]}', "amount": m["amount"],
                                    "kind": "move", "note": f'{m["from"]} → {m["to"]}', "done": bool(m["done"]) and d <= today})
        d += timedelta(days=14)
    for bill in BILLS:
        for due in _due_dates(bill, a, b):
            back = SAVED_BILLS.get(bill["what"])
            if back and due >= SAVED_FROM:  # its money comes back from Chase Savings a few days before
                out.append({"date": (due - timedelta(days=3)).isoformat(), "what": f'Move: {bill["what"]} back to checking',
                            "amount": back, "kind": "move", "note": "Chase Savings ··6725 → Chase checking ··7372",
                            "done": due < today})
            out.append({"date": due.isoformat(), "what": bill["what"].replace(" autopay", ""), "amount": bill["amount"],
                        "kind": "bill", "note": "on a card" if bill.get("card") else "",
                        "done": due <= today + timedelta(days=1) and _paid_near(conn, bill, due)})
    y, m = a.year, a.month
    while date(y, m, 1) < b:
        loan = date(y, m, 28)
        if loan > date(2026, 11, 30) and a <= loan < b:  # from Ally Spending after Nov
            out.append({"date": loan.isoformat(), "what": "Student loan", "amount": 699.33, "kind": "bill",
                        "note": "autopay from Ally Spending", "done": loan < today})
        mid = date(y, m, 20)
        if a <= mid < b:
            for w, x in OTHER_MONTHLY:
                out.append({"date": mid.isoformat(), "what": w, "amount": x, "kind": "bill", "note": "around the 20th",
                            "done": mid < today})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    for when, what, amt, _ in FROM_ALLY_KNOWN:
        if a <= when < b:
            out.append({"date": when.isoformat(), "what": what, "amount": amt, "kind": "bill", "note": "on a card; Ally pays it back",
                        "done": when < today})
    for x in PRIVATE_SUBS:
        if a <= x["due"] < b:
            out.append({"date": x["due"].isoformat(), "what": x["what"], "amount": x["amount"], "kind": "bill",
                        "note": "on Chase Sapphire; a bill, not spending money", "done": x["due"] < today})
    for t0, t1, what, gas in TRIPS:
        if a <= t0 < b:
            out.append({"date": t0.isoformat(), "what": f"Drive: {what}", "amount": gas, "kind": "trip", "note": "gas budgeted",
                        "done": t1 < today})
    out.sort(key=lambda e: (e["date"], {"payday": 0, "move": 1, "bill": 2, "trip": 3}[e["kind"]]))
    return out


# ---- Paycheck by paycheck ---------------------------------------------------------------
# His wallet rules (Nov 22-23):
#  - Whatever a paycheck leaves stays in the wallet and carries into the next one. Negative is
#    allowed: it means cut back until the wallet is green again.
#  - A card charge leaves the wallet the moment it hits, pending or not. Paying a card is not a
#    wallet event; card payments are never money in. Card rewards and statement credits are.
#  - Envelopes come out of the wallet: Black Friday Fund transfers ($450), student loan saving
#    ($350), the payday moves ($550 rent, $250 Ally). What an envelope buys is shown on its own
#    and never taken from the wallet twice. When checking fronts an envelope's purchase, the
#    envelope pays checking back from Chase Savings; that transfer isn't wallet money either.
#  - Each actual card ends with a check against the bank: checking less cards, plus what the
#    envelopes still owe checking, should equal the wallet.
WALLET_FROM = date(2026, 10, 30)  # October's bonus paycheck; the chain starts that morning
FUND_MERCHANTS = ("amazon", "micro center", "microperfume", "logitech")
FUND_TOPUPS = [(date(2026, 11, 22), 75.00), *FUND_REFILLS]  # decided in chat; counts until the transfer shows up
# Fund buys paid partly in cash, which no bank sees. The Micro Center receipt (Nov 8) totals $709.14:
# $409.14 on the Samsung card and $300 in cash from the Costco returns. Shown only; no bank effect.
FUND_CASH = [(date(2026, 11, 8), "Micro Center, cash part", 300.00)]
OTHER_MONTHLY = [("Kitty shot, your half", 66.50), ("Prescription", 24.67)]  # not in BILLS; due around the 20th
FROM_CHASE_SAVINGS = r"from sav|6725"


def _move_kind(amount: float) -> str:
    """What a transfer out of checking was for, by his amounts."""
    for target, kind in ((450, "fund"), (425, "fund"), (350, "Saved for the student loan"),
                         (TO_CHASE_SAVINGS, "To Chase Savings for rent"), (TO_ALLY, "To Ally Savings"),
                         (TO_ALLY + LOAN_TO_ALLY, "To Ally Savings (bills + loan)")):
        if abs(amount - target) < 1:
            return kind
    return "Moved to savings"


def _real(conn, day: date) -> float:
    """Checking less every card balance (pending included) on the morning of `day`."""
    since = day.isoformat()
    total = 0.0
    for a in conn.execute("SELECT id, mask, type, current_balance, available_balance FROM accounts"
                          " WHERE mask = ? OR type = 'credit'", (BILLS_ACCOUNT,)).fetchall():
        moved = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE account_id = ?"
                             " AND COALESCE(authorized_date, date) >= ?", (a["id"], since)).fetchone()[0]
        if a["type"] == "credit":
            held = 0.0 if a["mask"] in BALANCE_HAS_PENDING else conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE account_id = ? AND pending = 1",
                (a["id"],)).fetchone()[0]
            total -= float(a["current_balance"] or 0) - held + moved
        else:
            bal = a["available_balance"] if a["available_balance"] is not None else a["current_balance"]
            total += float(bal or 0) - moved
    return total


def _is_fund_buy(r) -> bool:
    name = (r["what"] or "").lower() + " | " + (r["raw_name"] or "").lower()
    return float(r["amount"]) < 0 and _is_covered(r) and any(m in name for m in FUND_MERCHANTS)


def _envelope_owed(conn, upto: date) -> float:
    """What envelopes owe checking on the morning of `upto`: Black Friday buys on the cards and rent
    paid from checking once it's saved in Chase Savings, less what Chase Savings has sent back."""
    rows = _rows(conn, WALLET_FROM, upto)
    acct = conn.execute("SELECT id FROM accounts WHERE mask = ?", (BILLS_ACCOUNT,)).fetchone()
    mine = {r[0] for r in conn.execute("SELECT plaid_transaction_id FROM transactions WHERE account_id = ?", (acct["id"],))}
    rent = next(b for b in BILLS if b["what"] == "Rent")
    owed = sum(-float(r["amount"]) for r in rows if _is_fund_buy(r))
    for r in rows:
        if r["pid"] not in mine:
            continue
        amt, name = float(r["amount"]), (r["what"] or "").lower() + " | " + (r["raw_name"] or "").lower()
        if amt < 0 and r["date"] >= SAVED_FROM.isoformat() and rent["lo"] <= -amt <= rent["hi"] and re.search(rent["match"], name):
            owed += -amt
        if amt > 0 and r["transfer_kind"] == "self_transfer" and re.search(FROM_CHASE_SAVINGS, name):
            owed -= amt
    return round(owed, 2)


def _flow(conn, a: date, b: date, today: date, carried: float) -> dict:
    """One paycheck's money in and out. Past days are actual; days still ahead are planned."""
    past = a <= today
    lines_in = [["Carried over in the wallet", round(carried, 2), False]]
    lines_out = []
    fund_in, env = 0.0, []
    s = summary(conn, a)
    bills = upcoming_bills(conn, a, s, 0.0)["now"]["items"]
    tops = [x for d, x in FUND_TOPUPS if a <= d < b]
    if past:
        rows = _rows(conn, a, b)
        acct = conn.execute("SELECT id FROM accounts WHERE mask = ?", (BILLS_ACCOUNT,)).fetchone()
        mine = {r["plaid_transaction_id"] for r in conn.execute(
            "SELECT plaid_transaction_id FROM transactions WHERE account_id = ? AND COALESCE(authorized_date, date) >= ?"
            " AND COALESCE(authorized_date, date) < ?", (acct["id"], a.isoformat(), b.isoformat()))} if acct else set()
        chk = [r for r in rows if r["pid"] in mine]
        pay = sum(float(r["amount"]) for r in chk if r["pfc_detailed"] == "INCOME_SALARY")
        back = sum(float(r["amount"]) for r in chk if float(r["amount"]) > 0 and r["pfc_detailed"] != "INCOME_SALARY"
                   and r["transfer_kind"] not in ("self_transfer", "card_payment"))
        rewards = sum(float(r["amount"]) for r in rows if r["account_type"] == "credit" and float(r["amount"]) > 0
                      and _is_spending(r) and _counts_as_payback(r))
        from_ally = sum(float(r["amount"]) for r in chk if float(r["amount"]) > 0 and r["transfer_kind"] == "self_transfer"
                        and not re.search(FROM_CHASE_SAVINGS, (r["what"] or "").lower() + (r["raw_name"] or "").lower()))
        lines_in.append(["Paycheck", round(pay or PAYCHECK, 2), not pay])
        if back:
            lines_in.append(["Paid back to you", round(back, 2), False])
        if rewards:
            lines_in.append(["Card rewards and credits", round(rewards, 2), False])
        if from_ally:
            lines_in.append(["Back from Ally for a bill it covers", round(from_ally, 2), False])
        moves = [-float(r["amount"]) for r in chk if float(r["amount"]) < 0 and r["transfer_kind"] == "self_transfer"]
        for rt in routed(conn, a):
            hit = next((m for m in moves if abs(m - rt["amount"]) < 1), None)
            if hit is not None:
                moves.remove(hit)
            lines_out.append(["To " + rt["what"], rt["amount"], hit is None])
        for x in list(tops):  # a top-up he has already made shows up as a transfer
            if any(abs(m - x) < 0.01 for m in moves):
                tops.remove(x)
        other_moves = {}
        for m in moves:
            k = _move_kind(m) if m not in [t for _, t in FUND_TOPUPS] else "fund"
            if k == "fund":
                fund_in += m
            else:
                other_moves[k] = other_moves.get(k, 0) + m
        lines_out += [[k, round(v, 2), False] for k, v in other_moves.items()]
        for i in bills:
            if i.get("move") and not i["paid"]:
                lines_out.append([i["what"], i["amount"], True])
        # Bills by what actually hit in these dates; ones not here yet are added only if still due.
        hit, other = {}, 0.0
        for r in rows:
            if _is_fund_buy(r):
                env.append({"what": r["what"], "envelope": "Black Friday Fund", "date": r["date"],
                            "amount": round(-float(r["amount"]), 2), "est": False})
                continue
            if float(r["amount"]) >= 0 or _is_spending(r) or r["is_transfer"] or r["transfer_kind"] in ("card_payment", "self_transfer"):
                continue
            if r["account_type"] != "credit" and r["pid"] not in mine:
                continue  # Ally pays its own bills from the money moved there
            amt = -float(r["amount"])
            name = (r["what"] or "").lower() + " | " + (r["raw_name"] or "").lower()
            bill = next((x for x in BILLS if any(re.search(x["match"], part.strip()) for part in name.split("|"))
                         and x.get("each_lo", x["lo"]) <= amt <= x.get("each_hi", x["hi"])), None)
            if not bill:
                other += amt
            elif bill["what"] == "Rent" and r["date"] >= SAVED_FROM.isoformat():
                env.append({"what": "Rent", "envelope": "Chase Savings", "date": r["date"], "amount": amt, "est": False})
            else:
                hit[bill["what"]] = hit.get(bill["what"], 0) + amt
        lines_out += [[w, round(x, 2), False] for w, x in hit.items()]
        for i in bills:
            if i.get("move") or i["paid"] or i["what"] in hit or i["due"] < today.isoformat():
                continue
            if i["saved"]:
                env.append({"what": i["what"], "envelope": "Chase Savings", "date": i["due"], "amount": i["amount"], "est": True})
            else:
                lines_out.append([i["what"], i["amount"], True])
        if other >= 0.01:
            lines_out.append(["Other charges (Kitty, vet, one-offs)", round(other, 2), False])
        still = max(s["left"], 0.0) if a <= today < b else 0.0
        e = needs_effect(conn, a, b, today)  # with today's view of trips, as the bank check uses
        if e:
            lines_out.append(["Groceries & gas (set aside)", e["set_aside"], False])
            lines_out.append(["Spending money", round(_cost(rows) - e["spent"] + e["over"] - e["cashin"] + still, 2), bool(still)])
        else:
            lines_out.append(["Spending money", round(_cost(rows) + still, 2), bool(still)])
        ahead = still + sum(l[1] for l in lines_out if l[2] and l[0] != "Spending money")
    else:
        lines_in.append(["Paycheck", PAYCHECK, True])
        for i in bills:
            if i.get("move"):
                lines_out.append(["To " + i["what"][3:], i["amount"], True])
            elif i["saved"]:
                env.append({"what": i["what"], "envelope": "Chase Savings", "date": i["due"], "amount": i["amount"], "est": True})
            else:
                lines_out.append([i["what"], i["amount"], True])
        if a <= date(a.year, a.month, 20) < b:
            lines_out += [[w, amt, True] for w, amt in OTHER_MONTHLY]
        env_cut = sum(needs_budget(conn, n, a, b, today) for n in NEEDS_MONTHLY) if a >= NEEDS_FROM else 0.0
        if env_cut:
            lines_out.append(["Groceries & gas (set aside)", env_cut, True])
        for rt in routed(conn, a):
            lines_out.append(["To " + rt["what"], rt["amount"], True])
        lines_out.append(["Spending money", round(PER_PERIOD - SET_ASIDE.get(a, 0.0) - env_cut
                                                  - sum(rt["amount"] for rt in routed(conn, a)), 2), True])
        ahead = 0.0
    ahead += sum(tops)
    fund_in += sum(tops)
    if fund_in:
        lines_out.append(["Into the Black Friday Fund", round(fund_in, 2), bool(tops)])
    lines_out.sort(key=lambda l: -l[1])
    if carried < 0:  # owed from before: money out, not negative money in
        lines_in = lines_in[1:]
        lines_out.insert(0, ["Short from before", round(-carried, 2), False])
    tin, tout = sum(l[1] for l in lines_in), sum(l[1] for l in lines_out)
    held = [{"what": w, "amount": x} for w, x in FUND_HELD] if a <= today < b else []
    env += [{"what": w, "envelope": "Black Friday Fund", "date": d.isoformat(), "amount": x, "est": False, "cash": True}
            for d, w, x in FUND_CASH if a <= d < b]
    env.sort(key=lambda e: e["date"])
    # Fund money moving between savings accounts: it stays in the fund, it doesn't pay checking back.
    moved = [{"what": "Moved from Ally into Chase Savings", "date": r["date"], "amount": round(float(r["amount"]), 2)}
             for r in conn.execute("""SELECT t.date, t.amount FROM transactions t JOIN accounts a ON a.id = t.account_id
                 WHERE a.mask = '6725' AND t.amount > 0 AND lower(t.name) LIKE '%ally%'
                 AND COALESCE(t.authorized_date, t.date) >= ? AND COALESCE(t.authorized_date, t.date) < ?""",
                 (a.isoformat(), b.isoformat()))]
    return {"payday": a.isoformat(), "to": (b - timedelta(days=1)).isoformat(), "actual": past,
            "carried": round(carried, 2), "ahead": round(ahead, 2),
            "in": [{"what": w, "amount": round(x, 2), "est": e} for w, x, e in lines_in],
            "out": [{"what": w, "amount": round(x, 2), "est": e} for w, x, e in lines_out],
            "total_in": round(tin, 2), "total_out": round(tout, 2), "left": round(tin - tout, 2),
            "envelopes": {"in": round(fund_in, 2), "paid": sorted(env, key=lambda e: e["date"]), "held": held, "moved": moved}}


def paycheck_flow(conn, today: date) -> list[dict]:
    """The wallet paycheck by paycheck from WALLET_FROM, each carrying into the next, with a
    bank check on each actual one. Returns last month's two paychecks and the next two."""
    start, _ = period_for(today)
    out, carried, a = [], _real(conn, WALLET_FROM), WALLET_FROM
    while a <= start + timedelta(days=28):
        b = a + timedelta(days=14)
        f = _flow(conn, a, b, today, carried)
        if f["actual"]:
            at = min(b, today + timedelta(days=1))
            wallet_then = f["left"] + f["ahead"]  # today, before what's still to come
            owed = _envelope_owed(conn, at)
            real = _real(conn, at)
            needs = envelope_balance(conn, at, today) if at > NEEDS_FROM else 0.0
            f["check"] = {"date": at.isoformat(), "wallet": round(wallet_then, 2), "bank": round(real, 2),
                          "owed": owed, "needs": needs, "off": round(wallet_then - real - owed + needs, 2)}
        out.append(f)
        carried, a = f["left"], b
    return out[-4:]


def envelope_payback(conn, today: date) -> float:
    """What Chase Savings should send checking now for envelope buys checking fronted."""
    return _envelope_owed(conn, today + timedelta(days=1))
