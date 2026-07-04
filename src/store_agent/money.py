"""Exact money math.

All rule arithmetic happens in Decimal with half-up cent rounding (data
dictionary rule 2). SQLite stores money as REAL for query ergonomics; values
cross into Decimal through D() at the Python boundary, and cross back out as
canonical "12.34" strings so the LLM never sees float artifacts.
"""

from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")


def D(value) -> Decimal:
    return Decimal(str(value))


def to_cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def money_str(value) -> str:
    return f"{to_cents(D(value)):.2f}"


def discounted_unit_price(unit_price, order_discount_pct) -> Decimal:
    """Rule 2: price actually paid per unit, rounded to the cent half-up."""
    price = D(unit_price) * (Decimal(1) - D(order_discount_pct) / Decimal(100))
    return to_cents(price)


def percent_off(price, pct) -> Decimal:
    return to_cents(D(price) * (Decimal(1) - D(pct) / Decimal(100)))
