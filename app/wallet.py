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
SET_ASIDE = {date(2026, 10, 30): 425.00, date(2026, 11, 13): 425.00}
SET_ASIDE_LABEL = "for the Black Friday Fund"

# The Black Friday Fund as he runs it (his update, 2026-11-20). The real money is the $450 he
# moved Chase -> Ally Spending on Nov 16 and back to Chase checking on Nov 20 (lands Nov 23);
# that transfer is not spending money. The first $450 bought the monitor and is done.
# SET_ASIDE above is only what came off his spending money; this is what the fund holds.
# Plus $75 he added on Nov 22 from unassigned checking (to Chase Savings) for two more items.
FUND_SAVED = 525.00
FUND_SPENT = [
    ("Trimmer + socks, Amazon", 143.30),
    ("Prime, one month (ends Dec 20)", 14.99),
    # Order sp-5798420, $37.64: $34.68 on a MicroPerfumes gift card, the rest on Sapphire.
    ("MicroPerfumes, after the gift card", 2.96),
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
    ("RAINHOL ironing board, black", 48.44, "Lowest price ever."),
    ("SoliSnug desk leg heater", 62.99, "Buy under $62.99."),
    ("Rolling under-bed bins, 2-pack", 33.14, "Only if you need more room."),
    ("Babbel Lifetime", 199.20, "Buy at $159 or less. Probably won't fit the fund."),
]
# The fund is temporary: its home tile goes away after this day.
FUND_SHOW_UNTIL = date(2027, 1, 31)

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


def is_grocery(row) -> bool:
    if (row.get("pfc_detailed") or "") in NEEDS["Groceries"][1]:
        return True
    raw = ((row.get("raw_name") or "") + " " + (row.get("what") or "")).lower()
    return any(x in raw for x in GROCERY_LIKE)


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
    {"what": "Student loan autopay", "amount": 699.33, "day": 28, "start": date(2026, 11, 28), "match": r"aidvantage|dept ?of ?ed|nelnet|mohela", "lo": 650, "hi": 760},
    {"what": "State Farm", "amount": 253.80, "day": 6, "match": r"state farm", "lo": 200, "hi": 270},
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
        if start <= d < end and d >= b.get("start", d):
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
    spend_next = PER_PERIOD - SET_ASIDE.get(nxt, 0.0)
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
    {"merchant": "amazon", "low": 1.00, "high": 160.00, "after": date(2026, 11, 19), "before": date(2026, 11, 27)},
    {"merchant": "amzn", "low": 1.00, "high": 160.00, "after": date(2026, 11, 19), "before": date(2026, 11, 27)},
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
    if float(row["amount"]) <= 0:
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
            " n.tag AS tag, a.type AS account_type,"
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


def summary(conn, today: date | None = None) -> dict:
    today = today or date.today()
    start, nxt = period_for(today)
    prev_start = start - timedelta(days=14)

    set_aside = SET_ASIDE.get(start, 0.0)
    prev_set = SET_ASIDE.get(prev_start, 0.0)
    moved_in = sum(a for (_, _, a), payday in MOVED if payday == start)
    prev_moved_in = sum(a for (_, _, a), payday in MOVED if payday == prev_start)

    # One paycheck of carry over, exactly as his page does it.
    prev_rows = _rows(conn, prev_start, start)
    prev_cap = PER_PERIOD - prev_set - prev_moved_in
    roll = prev_cap - _cost(prev_rows) + _paid_back(prev_rows)

    cap = PER_PERIOD - set_aside - moved_in + roll
    rows = _rows(conn, start, nxt)
    spent = _cost(rows)
    paid_back = _paid_back(rows)
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
        "allowance": round(cap, 2),
        "spent": round(spent, 2),
        "paid_back": round(paid_back, 2),
        "left": round(left, 2),
        "tip_estimate": round(tips, 2),
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
    {"what": "Student loan", "group": "Loans", "amount": 699.33, "from": "2026-11", "match": r"aidvantage|dept ?of ?ed", "lo": 650, "hi": 760},
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
    {"what": "3-month subscription", "group": "Subscriptions", "amount": round(244.38 * 365 / 90 / 12, 2), "from": "2026-11", "note": "$244.38 with tax every 90 days, on a card the app can't see"},
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
        moves.append({"id": f"pay-{start}-ally", "kind": "payday", "bank": "ally", "date": start.isoformat(),
                      "what": "Quarterly and yearly bills", "amount": TO_ALLY,
                      "from": "Chase checking ··7372", "to": "Ally Savings ··2617",
                      "how": "Ally → Transfers → from your Chase checking into Ally Savings. Takes 1–3 days.",
                      "done": _moved(conn, BILLS_ACCOUNT, r"ally", TO_ALLY - 1, a, b, -1)
                              or _moved(conn, ALLY_SAVINGS, r"jpmorgan|chase", TO_ALLY - 1, a, b, 1)
                              or _moved(conn, ALLY_SPENDING, r"jpmorgan|chase", TO_ALLY - 1, a, b, 1)})
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
FUND_MERCHANTS = ("amazon", "micro center", "microperfume")
FUND_TOPUPS = [(date(2026, 11, 22), 75.00)]  # decided in chat; counts until the transfer shows up
# Fund buys paid partly in cash, which no bank sees. The Micro Center receipt (Nov 8) totals $709.14:
# $409.14 on the Samsung card and $300 in cash from the Costco returns. Shown only; no bank effect.
FUND_CASH = [(date(2026, 11, 8), "Micro Center, cash part", 300.00)]
OTHER_MONTHLY = [("Kitty shot, your half", 66.50), ("Prescription", 24.67)]  # not in BILLS; due around the 20th
FROM_CHASE_SAVINGS = r"from sav|6725"


def _move_kind(amount: float) -> str:
    """What a transfer out of checking was for, by his amounts."""
    for target, kind in ((450, "fund"), (425, "fund"), (350, "Saved for the student loan"),
                         (TO_CHASE_SAVINGS, "To Chase Savings for rent"), (TO_ALLY, "To Ally Savings")):
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
        lines_out.append(["Spending money", round(PER_PERIOD - SET_ASIDE.get(a, 0.0), 2), True])
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
            f["check"] = {"date": at.isoformat(), "wallet": round(wallet_then, 2), "bank": round(real, 2),
                          "owed": owed, "off": round(wallet_then - real - owed, 2)}
        out.append(f)
        carried, a = f["left"], b
    return out[-4:]


def envelope_payback(conn, today: date) -> float:
    """What Chase Savings should send checking now for envelope buys checking fronted."""
    return _envelope_owed(conn, today + timedelta(days=1))
