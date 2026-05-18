from __future__ import annotations

from datetime import date, timedelta


def _third_friday_of_month(year: int, month: int) -> date:
    """US equity monthly options expiration: third Friday of the month."""
    first = date(year, month, 1)
    days_until_friday = (4 - first.weekday()) % 7
    return first + timedelta(days=days_until_friday + 14)


def get_next_opex(today: date) -> tuple[date, int]:
    """Return (next OPEX date, calendar days until that date from *today*).

  If *today* is on or before this month's third Friday, that date is used;
  otherwise the third Friday of the following month.
    """
    opex = _third_friday_of_month(today.year, today.month)
    if today > opex:
        if today.month == 12:
            opex = _third_friday_of_month(today.year + 1, 1)
        else:
            opex = _third_friday_of_month(today.year, today.month + 1)
    return opex, (opex - today).days
