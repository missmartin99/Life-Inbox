"""Receipts -> a simple expense tracker.

Receipts are just smart cards (see app/item_kinds.py) with kind = "receipt".
A photo of a bill already gets its text from OCR, so making a card from it
gives us vendor, amount, date and category. This file:

  * finds amounts and dates in plain text (used when AI is not available);
  * adds up receipts per month and per category.

No new table is needed: the receipt fields live in inbox_items.kind_fields.
"""

import re
from datetime import datetime

CATEGORIES = ["Food", "Groceries", "Transport", "Shopping", "Bills", "Health", "Other"]

# "₹1,250.50", "Rs. 300", "INR 99", "Total: 450"
AMOUNT_RE = re.compile(
    r"(?:₹|rs\.?|inr|total[:\s]*|amount[:\s]*)\s*(\d[\d,]*(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
# 05/11/2026, 5-11-2026, or 2026-11-05
DATE_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b|\b(\d{4})-(\d{2})-(\d{2})\b")


def find_amount(text):
    """The biggest money amount in the text (usually the total), or None."""
    amounts = [float(m.replace(",", "")) for m in AMOUNT_RE.findall(text)]
    return max(amounts) if amounts else None


def find_date(text):
    """The first date in the text as "YYYY-MM-DD" (Indian day/month order)."""
    match = DATE_RE.search(text)
    if not match:
        return None
    if match.group(4):
        day, month, year = match.group(6), match.group(5), match.group(4)
    else:
        day, month, year = match.group(1), match.group(2), match.group(3)
    try:
        return datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d")
    except ValueError:
        return None


def receipt_month(item):
    """Which month a receipt belongs to: its own date, else when it was saved."""
    date = (item.get("kind_fields") or {}).get("date") or ""
    if re.match(r"^\d{4}-\d{2}", date):
        return date[:7]
    return item["created_at"][:7]


def expense_summary(receipt_items, month=None):
    """Add up receipts. `month` ("2026-11") limits it to one month."""
    total = 0.0
    by_month = {}
    by_category = {}
    receipts = []

    for item in receipt_items:
        fields = item.get("kind_fields") or {}
        amount = fields.get("amount")
        if not isinstance(amount, (int, float)):
            continue  # a receipt without an amount can't be counted
        item_month = receipt_month(item)
        if month and item_month != month:
            continue
        category = fields.get("category") if fields.get("category") in CATEGORIES else "Other"

        total += amount
        by_month[item_month] = by_month.get(item_month, 0) + amount
        by_category[category] = by_category.get(category, 0) + amount
        receipts.append(
            {
                "item_id": item["id"],
                "vendor": fields.get("vendor"),
                "amount": amount,
                "date": fields.get("date"),
                "category": category,
                "month": item_month,
            }
        )

    return {
        "month": month,
        "count": len(receipts),
        "total": round(total, 2),
        "by_month": {k: round(v, 2) for k, v in sorted(by_month.items())},
        "by_category": {k: round(v, 2) for k, v in sorted(by_category.items())},
        "receipts": receipts,
    }
