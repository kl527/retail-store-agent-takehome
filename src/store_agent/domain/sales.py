"""Ringing up sales and reading orders back."""

import sqlite3
from collections import OrderedDict
from decimal import Decimal

from ..db import next_order_id
from ..errors import DomainError
from ..money import D, discounted_unit_price, money_str
from . import catalog
from .dates import validate_date
from .pricing import effective_unit_price
from .quantities import whole_quantity


def ring_up_sale(
    conn: sqlite3.Connection,
    items: list[dict],
    sale_date: str,
    customer_id: str | None = None,
    order_discount_pct=0,
    payment_method: str = "cash",
) -> dict:
    """Record a sale atomically: stock check, promo pricing, inventory decrement.

    Refuses the whole sale if any line lacks stock — no partial sales.
    """
    validate_date(sale_date, "date")
    if payment_method not in ("cash", "card"):
        raise DomainError("payment_method must be 'cash' or 'card'", payment_method=payment_method)
    if not (Decimal(0) <= D(order_discount_pct) <= Decimal(100)):
        raise DomainError(
            "order_discount_pct must be between 0 and 100", order_discount_pct=order_discount_pct
        )
    if customer_id is not None:
        found = conn.execute(
            "SELECT 1 FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()
        if not found:
            raise DomainError(f"Unknown customer_id: {customer_id}", customer_id=customer_id)
    if not items:
        raise DomainError("A sale needs at least one item")

    # Merge duplicate SKUs so each order has at most one line per SKU — the
    # returns logic relies on this invariant (seed data satisfies it too).
    merged: OrderedDict[str, int] = OrderedDict()
    for item in items:
        sku, qty = catalog.resolve_sku(conn, item["sku"]), whole_quantity(item["quantity"])
        if qty <= 0:
            raise DomainError("quantity must be positive", sku=sku, quantity=qty)
        merged[sku] = merged.get(sku, 0) + qty

    shortages = []
    for sku, qty in merged.items():
        inv = conn.execute("SELECT on_hand_qty FROM inventory WHERE sku = ?", (sku,)).fetchone()
        if inv is None:
            raise DomainError(
                f"Unknown SKU: {sku} — call search_products to find the correct SKU",
                sku=sku,
            )
        if inv["on_hand_qty"] < qty:
            shortages.append({"sku": sku, "requested": qty, "on_hand": inv["on_hand_qty"]})
    if shortages:
        raise DomainError(
            "Insufficient stock — sale not recorded", shortages=shortages
        )

    order_id = next_order_id(conn)
    conn.execute(
        """INSERT INTO orders (order_id, order_date, customer_id, order_discount_pct, payment_method)
           VALUES (?, ?, ?, ?, ?)""",
        (order_id, sale_date, customer_id, float(D(order_discount_pct)), payment_method),
    )

    lines, total = [], D(0)
    for line_no, (sku, qty) in enumerate(merged.items(), start=1):
        unit_price, promo_id = effective_unit_price(conn, sku, sale_date)
        conn.execute(
            "INSERT INTO order_lines (order_id, line_no, sku, quantity, unit_price) VALUES (?, ?, ?, ?, ?)",
            (order_id, line_no, sku, qty, float(unit_price)),
        )
        conn.execute(
            "UPDATE inventory SET on_hand_qty = on_hand_qty - ? WHERE sku = ?", (qty, sku)
        )
        paid_unit = discounted_unit_price(unit_price, order_discount_pct)
        line_total = paid_unit * qty
        total += line_total
        name = conn.execute(
            "SELECT product_name, color, size FROM products WHERE sku = ?", (sku,)
        ).fetchone()
        lines.append(
            {
                "line_no": line_no,
                "sku": sku,
                "product": " ".join(filter(None, [name["product_name"], name["color"], name["size"]])),
                "quantity": qty,
                "unit_price": money_str(unit_price),
                "promo_applied": promo_id,
                "paid_unit_price": money_str(paid_unit),
                "line_total": money_str(line_total),
            }
        )
    conn.commit()
    return {
        "order_id": order_id,
        "order_date": sale_date,
        "customer_id": customer_id,
        "order_discount_pct": float(D(order_discount_pct)),
        "payment_method": payment_method,
        "lines": lines,
        "order_total": money_str(total),
    }


def get_order(conn: sqlite3.Connection, order_id: str) -> dict:
    order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    if order is None:
        raise DomainError(f"Unknown order: {order_id}", order_id=order_id)
    lines = []
    for row in conn.execute(
        """SELECT ol.*, p.product_name, p.color, p.size FROM order_lines ol
           JOIN products p ON p.sku = ol.sku
           WHERE ol.order_id = ? ORDER BY ol.line_no""",
        (order_id,),
    ):
        paid_unit = discounted_unit_price(row["unit_price"], order["order_discount_pct"])
        lines.append(
            {
                "line_no": row["line_no"],
                "sku": row["sku"],
                "product": " ".join(filter(None, [row["product_name"], row["color"], row["size"]])),
                "quantity": row["quantity"],
                "unit_price": money_str(row["unit_price"]),
                "paid_unit_price": money_str(paid_unit),
                "line_total": money_str(paid_unit * row["quantity"]),
            }
        )
    returns = [
        dict(r, refund_amount=money_str(r["refund_amount"]))
        for r in (
            dict(row)
            for row in conn.execute(
                "SELECT * FROM returns WHERE order_id = ? ORDER BY return_id", (order_id,)
            )
        )
    ]
    return {
        "order_id": order["order_id"],
        "order_date": order["order_date"],
        "customer_id": order["customer_id"],
        "order_discount_pct": order["order_discount_pct"],
        "payment_method": order["payment_method"],
        "lines": lines,
        "returns": returns,
    }


def list_orders(
    conn: sqlite3.Connection,
    customer_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    clauses, params = [], []
    if customer_id:
        clauses.append("customer_id = ?")
        params.append(customer_id)
    if start_date:
        clauses.append("order_date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("order_date <= ?")
        params.append(end_date)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    orders = conn.execute(
        f"SELECT order_id FROM orders {where} ORDER BY order_date, order_id", params
    ).fetchall()
    return [get_order(conn, o["order_id"]) for o in orders]
