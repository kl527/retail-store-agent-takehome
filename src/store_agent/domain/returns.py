"""Returns and refunds (rule 3)."""

import sqlite3

from ..db import next_return_id
from ..errors import DomainError
from ..money import discounted_unit_price, money_str
from . import catalog
from .dates import validate_date
from .quantities import whole_quantity


def process_return(
    conn: sqlite3.Connection,
    order_id: str,
    sku: str,
    quantity: int,
    condition: str,
    return_date: str,
) -> dict:
    """Refund the price actually paid (never current/list price).

    'good' units go back to on-hand stock; 'damaged' units do not.
    """
    validate_date(return_date, "date")
    if condition not in ("good", "damaged"):
        raise DomainError("condition must be 'good' or 'damaged'", condition=condition)
    quantity = whole_quantity(quantity)
    if quantity <= 0:
        raise DomainError("quantity must be positive", quantity=quantity)
    sku = catalog.resolve_sku(conn, sku)

    order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    if order is None:
        raise DomainError(f"Unknown order: {order_id}", order_id=order_id)
    line = conn.execute(
        "SELECT * FROM order_lines WHERE order_id = ? AND sku = ?", (order_id, sku)
    ).fetchone()
    if line is None:
        raise DomainError(
            f"Order {order_id} has no line with SKU {sku}", order_id=order_id, sku=sku
        )

    already = conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) AS q FROM returns WHERE order_id = ? AND sku = ?",
        (order_id, sku),
    ).fetchone()["q"]
    remaining = line["quantity"] - already
    if quantity > remaining:
        raise DomainError(
            f"Only {remaining} unit(s) of {sku} on {order_id} remain returnable "
            f"({line['quantity']} sold, {already} already returned)",
            sold=line["quantity"],
            already_returned=already,
            requested=quantity,
        )

    paid_unit = discounted_unit_price(line["unit_price"], order["order_discount_pct"])
    refund = paid_unit * quantity
    return_id = next_return_id(conn)
    conn.execute(
        """INSERT INTO returns (return_id, return_date, order_id, sku, quantity, condition, refund_amount)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (return_id, return_date, order_id, sku, quantity, condition, float(refund)),
    )
    restocked = condition == "good"
    if restocked:
        conn.execute(
            "UPDATE inventory SET on_hand_qty = on_hand_qty + ? WHERE sku = ?", (quantity, sku)
        )
    conn.commit()
    return {
        "return_id": return_id,
        "return_date": return_date,
        "order_id": order_id,
        "sku": sku,
        "quantity": quantity,
        "condition": condition,
        "paid_unit_price": money_str(paid_unit),
        "refund_amount": money_str(refund),
        "restocked": restocked,
    }
