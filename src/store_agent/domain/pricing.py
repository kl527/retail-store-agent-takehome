"""Effective pricing under promotions (rules 2 and 5)."""

import sqlite3
from decimal import Decimal

from ..errors import DomainError
from ..money import D, percent_off


def applicable_promotions(conn: sqlite3.Connection, sku: str, on_date: str) -> list[sqlite3.Row]:
    """Promotions whose scope covers this SKU and whose window contains the date."""
    return conn.execute(
        """SELECT pr.* FROM promotions pr
           JOIN products p ON p.sku = ?
           WHERE pr.start_date <= ? AND pr.end_date >= ?
             AND (
                  (pr.scope_type = 'product'  AND pr.scope_ref = p.product_id)
               OR (pr.scope_type = 'category' AND pr.scope_ref = p.category)
             )""",
        (sku, on_date, on_date),
    ).fetchall()


def effective_unit_price(conn: sqlite3.Connection, sku: str, on_date: str) -> tuple[Decimal, str | None]:
    """(price per unit on that date, promo_id applied or None).

    Rule 5: lowest resulting price wins when promotions overlap; no stacking.
    """
    row = conn.execute("SELECT retail_price FROM products WHERE sku = ?", (sku,)).fetchone()
    if row is None:
        raise DomainError(f"Unknown SKU: {sku}", sku=sku)
    retail = D(row["retail_price"])

    best_price, best_promo = retail, None
    for promo in applicable_promotions(conn, sku, on_date):
        price = percent_off(retail, promo["value"])
        if price < best_price:
            best_price, best_promo = price, promo["promo_id"]
    return best_price, best_promo


def simulate_discount_price(conn: sqlite3.Connection, sku: str, percent_off_value) -> Decimal:
    """Hypothetical price under an arbitrary percent-off — reads only, never persists.

    For 'what would X cost if...' questions, so the model isn't tempted to
    reach for create_promotion (which is real, persistent state) just to
    answer a hypothetical.
    """
    row = conn.execute("SELECT retail_price FROM products WHERE sku = ?", (sku,)).fetchone()
    if row is None:
        raise DomainError(f"Unknown SKU: {sku}", sku=sku)
    return percent_off(D(row["retail_price"]), percent_off_value)


def create_promotion(
    conn: sqlite3.Connection,
    description: str,
    percent_value,
    scope_type: str,
    scope_ref: str,
    start_date: str,
    end_date: str,
) -> dict:
    from ..db import next_promo_id

    if scope_type not in ("product", "category"):
        raise DomainError("scope_type must be 'product' or 'category'", scope_type=scope_type)
    if scope_type == "product":
        exists = conn.execute(
            "SELECT 1 FROM products WHERE product_id = ? LIMIT 1", (scope_ref,)
        ).fetchone()
        if not exists:
            raise DomainError(f"Unknown product_id: {scope_ref}", scope_ref=scope_ref)
    elif scope_ref not in ("apparel", "goods"):
        raise DomainError(f"Unknown category: {scope_ref}", scope_ref=scope_ref)
    pct = D(percent_value)
    if not (Decimal(0) < pct <= Decimal(100)):
        raise DomainError("percent_off must be in (0, 100]", value=str(percent_value))
    if end_date < start_date:
        raise DomainError("end_date is before start_date", start_date=start_date, end_date=end_date)

    promo_id = next_promo_id(conn)
    conn.execute(
        """INSERT INTO promotions (promo_id, description, type, value, scope_type, scope_ref, start_date, end_date)
           VALUES (?, ?, 'percent_off', ?, ?, ?, ?, ?)""",
        (promo_id, description, float(pct), scope_type, scope_ref, start_date, end_date),
    )
    conn.commit()
    return {
        "promo_id": promo_id,
        "description": description,
        "type": "percent_off",
        "value": float(pct),
        "scope_type": scope_type,
        "scope_ref": scope_ref,
        "start_date": start_date,
        "end_date": end_date,
    }
