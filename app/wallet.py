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

# Charges he decided belong to a LATER paycheck than the one they posted in: the
# Oct 26 Costco run came out of the Oct 30 check, not the Oct 16 one. Hidden in
# the period it posted in and taken off the top of the period it belongs to.
# (date, merchant fragment, amount) -> the payday it should land on.
MOVED = [
    ((date(2026, 10, 27), "costco", 227.92), date(2026, 10, 30)),
]
MOVED_LABEL = "for the Oct 26 Costco run"

# Already paid for out of money he had set aside, so it must not count twice.
# The Acer monitor: cash from his monitor returns plus money put by beforehand.
COVERED = [
    {"merchant": "micro center", "low": 395.00, "high": 425.00,
     "after": date(2026, 11, 7), "before": date(2026, 11, 22)},
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
)
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
        if (row["date"] == when.isoformat()
                and merchant in (row["what"] or "").lower()
                and abs(-float(row["amount"]) - amount) < 0.01):
            return payday
    return None


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
    if not row["pending"] or amount <= 0:
        return 0.0
    name = (row["what"] or "").lower()
    if any(x in name for x in NO_TIP):
        return 0.0
    detailed = row["pfc_detailed"] or ""
    primary = row["pfc_primary"] or ""
    if detailed in TIP_ALWAYS_DETAILED:
        return amount * TIP_RATE
    if primary in TIP_PRIMARY and amount >= TIP_MIN:
        return amount * TIP_RATE
    return 0.0


def _rows(conn, start: date, end: date) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT t.amount, t.date, t.pending, t.is_transfer, t.pfc_primary,"
            " t.pfc_detailed, t.transfer_kind, COALESCE(t.merchant_name, t.name) AS what,"
            " n.tag AS tag, a.type AS account_type"
            " FROM transactions t"
            " JOIN accounts a ON a.id = t.account_id"
            " LEFT JOIN transaction_notes n ON n.transaction_id = t.id"
            " WHERE t.date >= ? AND t.date < ?",
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
