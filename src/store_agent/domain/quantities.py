"""Whole-unit quantity validation.

Every quantity in this store is a count of discrete units — you can't sell,
return, order, or receive half a hoodie. Plain `int(value)` truncates a
fractional quantity silently (`int(1.5) == 1`, no error), which would
under-record a sale or return without anyone noticing. This makes that an
explicit rejection instead.
"""

from ..errors import DomainError


def whole_quantity(value, field: str = "quantity") -> int:
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        raise DomainError(f"{field} must be a whole number, got {value!r}", **{field: value})
    qty = int(as_float)
    if qty != as_float:
        raise DomainError(f"{field} must be a whole number, got {value!r}", **{field: value})
    return qty
