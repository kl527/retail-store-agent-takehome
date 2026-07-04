"""Analytics under the frozen definitions (rules 1, 6, 7)."""

import sqlite3
from decimal import Decimal

from ..errors import DomainError
from ..money import D, discounted_unit_price, money_str
from .dates import validate_date

# Rule 7 pins the trailing-30-day velocity window to May 2026.
VELOCITY_START = "2026-05-01"
VELOCITY_END = "2026-05-31"
VELOCITY_DAYS = 30
COVER_THRESHOLD_DAYS = 14


def _northwind_costs(conn: sqlite3.Connection) -> dict[str, Decimal]:
    """Rule 1: every unit's cost basis is the Northwind Supply unit cost."""
    rows = conn.execute(
        """SELECT sc.product_id, sc.unit_cost FROM supplier_catalog sc
           JOIN suppliers s ON s.supplier_id = sc.supplier_id
           WHERE s.supplier_name = 'Northwind Supply'"""
    ).fetchall()
    return {r["product_id"]: D(r["unit_cost"]) for r in rows}


def _paid_lines(conn: sqlite3.Connection, start_date: str, end_date: str):
    """Order lines in the window with the actually-paid unit price (rule 2)."""
    return conn.execute(
        """SELECT ol.order_id, ol.sku, ol.quantity, ol.unit_price,
                  o.order_discount_pct, p.product_id, p.product_name
           FROM order_lines ol
           JOIN orders o ON o.order_id = ol.order_id
           JOIN products p ON p.sku = ol.sku
           WHERE o.order_date BETWEEN ? AND ?""",
        (start_date, end_date),
    ).fetchall()


def _check_date_range(start_date: str, end_date: str) -> None:
    """A reversed range would silently read as SQL BETWEEN's empty set (zero
    rows, not an error) — that reads as "no revenue/sales that period" when
    it's really a swapped-date typo, so reject it explicitly instead."""
    validate_date(start_date, "start_date")
    validate_date(end_date, "end_date")
    if end_date < start_date:
        raise DomainError(
            "start_date is after end_date", start_date=start_date, end_date=end_date
        )


def revenue_report(conn: sqlite3.Connection, start_date: str, end_date: str) -> dict:
    """Rule 6: revenue = dollars paid on orders in the period; net subtracts
    refunds *issued* in the period."""
    _check_date_range(start_date, end_date)
    gross = Decimal(0)
    for line in _paid_lines(conn, start_date, end_date):
        paid = discounted_unit_price(line["unit_price"], line["order_discount_pct"])
        gross += paid * line["quantity"]
    refunds = Decimal(0)
    for row in conn.execute(
        "SELECT refund_amount FROM returns WHERE return_date BETWEEN ? AND ?",
        (start_date, end_date),
    ):
        refunds += D(row["refund_amount"])
    return {
        "start_date": start_date,
        "end_date": end_date,
        "gross_revenue": money_str(gross),
        "refunds_issued": money_str(refunds),
        "net_revenue": money_str(gross - refunds),
    }


def top_products_by_margin(
    conn: sqlite3.Connection, start_date: str, end_date: str, limit: int = 5
) -> dict:
    """Rule 6 margin per product. A returned-and-restocked unit is excluded
    from both revenue and cost; a damaged return stays in both (the rule's
    exclusion is only for restocked units — refunds hit net revenue instead).
    """
    _check_date_range(start_date, end_date)
    costs = _northwind_costs(conn)
    stats: dict[str, dict] = {}
    for line in _paid_lines(conn, start_date, end_date):
        pid = line["product_id"]
        entry = stats.setdefault(
            pid,
            {
                "product_id": pid,
                "product_name": line["product_name"],
                "units_sold": 0,
                "units_returned_to_stock": 0,
                "revenue": Decimal(0),
            },
        )
        paid = discounted_unit_price(line["unit_price"], line["order_discount_pct"])
        entry["units_sold"] += line["quantity"]
        entry["revenue"] += paid * line["quantity"]
        # Good-condition returns against this line: back out revenue at the
        # paid price and drop the units from the cost base.
        good = conn.execute(
            """SELECT COALESCE(SUM(quantity), 0) AS q FROM returns
               WHERE order_id = ? AND sku = ? AND condition = 'good'""",
            (line["order_id"], line["sku"]),
        ).fetchone()["q"]
        entry["units_returned_to_stock"] += good
        entry["revenue"] -= paid * good

    if not stats:
        raise DomainError(
            "No sales in that period", start_date=start_date, end_date=end_date
        )

    products = []
    for entry in stats.values():
        stayed_sold = entry["units_sold"] - entry["units_returned_to_stock"]
        cost = costs.get(entry["product_id"], Decimal(0)) * stayed_sold
        margin = entry["revenue"] - cost
        margin_pct = (
            float(round(margin / entry["revenue"] * 100, 1)) if entry["revenue"] else None
        )
        products.append(
            {
                "product_id": entry["product_id"],
                "product_name": entry["product_name"],
                "units_sold": entry["units_sold"],
                "units_returned_to_stock": entry["units_returned_to_stock"],
                "revenue": money_str(entry["revenue"]),
                "cost_of_units_stayed_sold": money_str(cost),
                "margin": money_str(margin),
                "margin_pct": margin_pct,
                "_margin_sort": margin,
            }
        )
    products.sort(key=lambda p: p["_margin_sort"], reverse=True)
    for p in products:
        del p["_margin_sort"]
    return {
        "start_date": start_date,
        "end_date": end_date,
        "note": "ranked by margin dollars; margin_pct included for reference",
        "products": products[: int(limit)],
    }


def stockout_report(conn: sqlite3.Connection) -> dict:
    """Rule 7: flag a product if any variant is at/below its reorder point OR
    days of cover < 14. Velocity window = May 2026 sales."""
    monthly_units: dict[str, int] = {}
    for row in conn.execute(
        """SELECT p.product_id, SUM(ol.quantity) AS units
           FROM order_lines ol
           JOIN orders o ON o.order_id = ol.order_id
           JOIN products p ON p.sku = ol.sku
           WHERE o.order_date BETWEEN ? AND ?
           GROUP BY p.product_id""",
        (VELOCITY_START, VELOCITY_END),
    ):
        monthly_units[row["product_id"]] = row["units"]

    products = []
    for prod in conn.execute(
        "SELECT DISTINCT product_id, product_name FROM products ORDER BY product_id"
    ):
        variants = conn.execute(
            """SELECT i.* FROM inventory i
               JOIN products p ON p.sku = i.sku
               WHERE p.product_id = ? ORDER BY i.sku""",
            (prod["product_id"],),
        ).fetchall()
        on_hand = sum(v["on_hand_qty"] for v in variants)
        below = [v["sku"] for v in variants if v["on_hand_qty"] <= v["reorder_point"]]
        units = monthly_units.get(prod["product_id"], 0)
        days_of_cover = round(on_hand / (units / VELOCITY_DAYS), 1) if units else None
        low_cover = days_of_cover is not None and days_of_cover < COVER_THRESHOLD_DAYS
        products.append(
            {
                "product_id": prod["product_id"],
                "product_name": prod["product_name"],
                "on_hand_total": on_hand,
                "units_sold_last_30d": units,
                "days_of_cover": days_of_cover,
                "variants_below_reorder_point": below,
                "about_to_stock_out": bool(below) or low_cover,
            }
        )
    return {
        "velocity_window": f"{VELOCITY_START}..{VELOCITY_END}",
        "flag_rule": f"below reorder point OR days of cover < {COVER_THRESHOLD_DAYS}",
        "products": products,
    }
