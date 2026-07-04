"""Calendar-date validation.

Dates travel through this system as plain YYYY-MM-DD strings — SQLite has
no DATE type, and comparisons/sorts are lexicographic. That means nothing
else in the stack notices a syntactically-plausible but nonexistent date
(e.g. "2026-02-30"); it would just get stored and compared as if it were
real. Every entry point that accepts a date calls this first.
"""

from datetime import date as _date

from ..errors import DomainError


def validate_date(value: str, field: str = "date") -> str:
    try:
        _date.fromisoformat(value)
    except (ValueError, TypeError):
        raise DomainError(f"{field} is not a valid YYYY-MM-DD date: {value!r}", **{field: value})
    return value
