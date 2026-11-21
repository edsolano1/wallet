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
FUND_SAVED = 450.00
FUND_SPENT = [
    ("Trimmer + socks, Amazon", 143.30),
    ("Prime, one month (ends Dec 20)", 14.99),
]
# Ordered but charged only when it ships: kept out of what's left so the fund doesn't look bigger.
FUND_HELD = [("Philips i9000 shaver", 218.18)]
# Money he expects back into the fund but hasn't seen land.
FUND_CREDIT_TO_CONFIRM = [("Amazon card credit", 16.00)]

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
    {"what": "Ring", "amount": 10.90, "day": 1, "match": r"^ring\b", "lo": 5, "hi": 25, "card": True},
    {"what": "Student loan autopay", "amount": 699.33, "day": 28, "start": date(2026, 11, 28), "match": r"aidvantage|dept ?of ?ed|nelnet|mohela", "lo": 650, "hi": 760},
    {"what": "State Farm", "amount": 233.50, "day": 10, "match": r"state farm", "lo": 200, "hi": 270},
    {"what": "Parking", "amount": 30.00, "day": 9, "match": r"cash app\*gee", "lo": 25, "hi": 35},
    {"what": "Car wash", "amount": 21.00, "every": 28, "anchor": date(2026, 11, 13), "match": r"cobblestone", "lo": 15, "hi": 30, "card": True},
    {"what": "GEICO, paid in full", "amount": 1257.50, "once": date(2026, 12, 26), "match": r"geico", "lo": 1000, "hi": 1500},
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
                items.append({"what": bill["what"], "due": due.isoformat(), "card": bool(bill.get("card")),
                              "amount": round(hit["amt"] if hit else bill["amount"], 2), "paid": bool(hit),
                              "est": bool(bill.get("est")) and not hit})
        items.sort(key=lambda i: i["due"])
        return items

    now_items, next_items = cycle(start, nxt), cycle(nxt, after)
    later = [i for i in cycle(after, after + timedelta(days=28)) if i["amount"] >= LATER_MIN]
    due_now = sum(i["amount"] for i in now_items if not i["paid"])
    spend_now = max(s["left"], 0.0)  # what's still his to spend before payday
    left_now = cash - card_total - due_now - spend_now

    pay = conn.execute("SELECT amount FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE a.mask = ?"
                       " AND t.pfc_detailed = 'INCOME_SALARY' AND t.amount > 0 ORDER BY t.date DESC LIMIT 1",
                       (BILLS_ACCOUNT,)).fetchone()
    paycheck = float(pay["amount"]) if pay else 0.0
    spend_next = PER_PERIOD - SET_ASIDE.get(nxt, 0.0)
    due_next = sum(i["amount"] for i in next_items if not i["paid"])
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
    "Groceries": (175.00, {"FOOD_AND_DRINK_GROCERIES", "GENERAL_MERCHANDISE_SUPERSTORES"}),
    "Gas": (75.00, {"TRANSPORTATION_GAS"}),
}

# Charges he decided belong to a LATER paycheck than the one they posted in: the
# Oct 26 Costco run came out of the Oct 30 check, not the Oct 16 one. Hidden in
# the period it posted in and taken off the top of the period it belongs to.
# (date, merchant fragment, amount) -> the payday it should land on.
MOVED = [
    ((date(2026, 10, 27), "costco", 227.92), date(2026, 10, 30)),
]
MOVED_LABEL = "for the Oct 26 Costco run"

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
]

# Already paid for out of money he had set aside, so it must not count twice.
# The Acer monitor: cash from his monitor returns plus money put by beforehand.
COVERED = [
    {"merchant": "micro center", "low": 395.00, "high": 425.00,
     "after": date(2026, 11, 7), "before": date(2026, 11, 22)},
    # Black Friday orders on Amazon, paid from the fund (Nov 20): the trimmer and socks
    # (charged as they ship) and a month of Prime. Any Amazon charge this size in this
    # window is taken as one of them.
    {"merchant": "amazon", "low": 1.00, "high": 160.00, "after": date(2026, 11, 19), "before": date(2026, 11, 27)},
    {"merchant": "amzn", "low": 1.00, "high": 160.00, "after": date(2026, 11, 19), "before": date(2026, 11, 27)},
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
        "rolled_over": round(roll, 2),
        "allowance": round(cap, 2),
        "spent": round(spent, 2),
        "paid_back": round(paid_back, 2),
        "left": round(left, 2),
        "tip_estimate": round(tips, 2),
    }
