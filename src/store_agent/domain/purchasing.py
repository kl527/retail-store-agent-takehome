"""Restocking: supplier selection (rule 4), purchase orders, receiving."""

import sqlite3
from collections import defaultdict

from ..db import next_po_id
from ..errors import DomainError
from ..money import money_str
from . import catalog

MAX_LEAD_DAYS = 10


def eligible_supplier(conn: sqlite3.Connection, product_id: str) -> sqlite3.Row | None:
    """Cheapest supplier that can deliver within MAX_LEAD_DAYS.

    Ties break on shorter lead time, then supplier_id, for determinism.
    """
    return conn.execute(
        """SELECT sc.*, s.supplier_name FROM supplier_catalog sc
           JOIN suppliers s ON s.supplier_id = sc.supplier_id
           WHERE sc.product_id = ? AND sc.lead_time_days <= ?
           ORDER BY sc.unit_cost, sc.lead_time_days, sc.supplier_id
           LIMIT 1""",
        (product_id, MAX_LEAD_DAYS),
    ).fetchone()


def resolve_supplier(conn: sqlite3.Connection, supplier_ref: str) -> sqlite3.Row:
    """Accept an exact supplier_id or an unambiguous (partial) name.

    Small models pass display names ('Northwind') as IDs; being forgiving here
    beats making them retry. When nothing matches, the error lists the valid
    suppliers so the model can self-correct.
    """
    row = conn.execute(
        "SELECT * FROM suppliers WHERE supplier_id = ?", (supplier_ref,)
    ).fetchone()
    if row is not None:
        return row
    matches = conn.execute(
        "SELECT * FROM suppliers WHERE lower(supplier_name) LIKE ?",
        (f"%{supplier_ref.lower()}%",),
    ).fetchall()
    if len(matches) == 1:
        return matches[0]
    known = ", ".join(
        f"{r['supplier_id']} ({r['supplier_name']})"
        for r in conn.execute("SELECT * FROM suppliers ORDER BY supplier_id")
    )
    raise DomainError(
        f"Unknown supplier: {supplier_ref}. Known suppliers: {known}",
        supplier=supplier_ref,
    )


def create_purchase_order(
    conn: sqlite3.Connection,
    supplier_id: str,
    items: list[dict],
    created_date: str,
) -> dict:
    supplier = resolve_supplier(conn, supplier_id)
    supplier_id = supplier["supplier_id"]
    if not items:
        raise DomainError("A purchase order needs at least one item")

    # Validate every line before writing anything, so a bad line can't leave
    # a half-created PO behind.
    resolved = []
    for item in items:
        sku, qty = catalog.resolve_sku(conn, item["sku"]), int(item["quantity"])
        if qty <= 0:
            raise DomainError("quantity must be positive", sku=sku, quantity=qty)
        product = conn.execute("SELECT * FROM products WHERE sku = ?", (sku,)).fetchone()
        cost_row = conn.execute(
            "SELECT unit_cost FROM supplier_catalog WHERE supplier_id = ? AND product_id = ?",
            (supplier_id, product["product_id"]),
        ).fetchone()
        if cost_row is None:
            raise DomainError(
                f"{supplier['supplier_name']} does not supply {product['product_id']}",
                supplier_id=supplier_id,
                product_id=product["product_id"],
            )
        resolved.append((sku, qty, cost_row["unit_cost"]))

    po_id = next_po_id(conn)
    conn.execute(
        "INSERT INTO purchase_orders (po_id, supplier_id, created_date, status) VALUES (?, ?, ?, 'open')",
        (po_id, supplier_id, created_date),
    )
    lines = []
    for line_no, (sku, qty, unit_cost) in enumerate(resolved, start=1):
        conn.execute(
            """INSERT INTO purchase_order_lines (po_id, line_no, sku, qty_ordered, qty_received, unit_cost)
               VALUES (?, ?, ?, ?, 0, ?)""",
            (po_id, line_no, sku, qty, unit_cost),
        )
        lines.append(
            {
                "line_no": line_no,
                "sku": sku,
                "qty_ordered": qty,
                "qty_received": 0,
                "unit_cost": money_str(unit_cost),
            }
        )
    conn.commit()
    return {
        "po_id": po_id,
        "supplier_id": supplier_id,
        "supplier_name": supplier["supplier_name"],
        "created_date": created_date,
        "status": "open",
        "lines": lines,
    }


def restock_below_reorder(conn: sqlite3.Connection, created_date: str) -> dict:
    """Scan inventory, order reorder_qty of every SKU at/below its reorder
    point from the best eligible supplier; one PO per supplier."""
    low = conn.execute(
        """SELECT i.*, p.product_id, p.product_name FROM inventory i
           JOIN products p ON p.sku = i.sku
           WHERE i.on_hand_qty <= i.reorder_point
           ORDER BY i.sku"""
    ).fetchall()

    by_supplier: dict[str, list[dict]] = defaultdict(list)
    decisions, skipped = [], []
    for row in low:
        supplier = eligible_supplier(conn, row["product_id"])
        if supplier is None:
            skipped.append(
                {
                    "sku": row["sku"],
                    "reason": f"no supplier can deliver within {MAX_LEAD_DAYS} days",
                }
            )
            continue
        by_supplier[supplier["supplier_id"]].append(
            {"sku": row["sku"], "quantity": row["reorder_qty"]}
        )
        decisions.append(
            {
                "sku": row["sku"],
                "on_hand": row["on_hand_qty"],
                "reorder_point": row["reorder_point"],
                "ordered_qty": row["reorder_qty"],
                "supplier": supplier["supplier_name"],
                "unit_cost": money_str(supplier["unit_cost"]),
                "lead_time_days": supplier["lead_time_days"],
            }
        )

    pos = [
        create_purchase_order(conn, supplier_id, items, created_date)
        for supplier_id, items in by_supplier.items()
    ]
    return {"purchase_orders": pos, "decisions": decisions, "skipped": skipped}


def list_purchase_orders(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    clauses, params = [], []
    if status:
        clauses.append("po.status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    result = []
    for po in conn.execute(
        f"""SELECT po.*, s.supplier_name FROM purchase_orders po
            JOIN suppliers s ON s.supplier_id = po.supplier_id
            {where} ORDER BY po.po_id""",
        params,
    ):
        lines = [
            {
                "line_no": l["line_no"],
                "sku": l["sku"],
                "qty_ordered": l["qty_ordered"],
                "qty_received": l["qty_received"],
                "unit_cost": money_str(l["unit_cost"]),
            }
            for l in conn.execute(
                "SELECT * FROM purchase_order_lines WHERE po_id = ? ORDER BY line_no",
                (po["po_id"],),
            )
        ]
        result.append(
            {
                "po_id": po["po_id"],
                "supplier_id": po["supplier_id"],
                "supplier_name": po["supplier_name"],
                "created_date": po["created_date"],
                "status": po["status"],
                "lines": lines,
            }
        )
    return result


def receive_purchase_order(
    conn: sqlite3.Connection,
    po_id: str,
    received_date: str,
    receipts: list[dict] | None = None,
) -> dict:
    """Book a delivery: bump qty_received and on-hand stock.

    receipts=[{sku, quantity}, ...] for a partial delivery; omit to receive
    everything still outstanding. The PO closes when all lines are complete.
    """
    po = conn.execute("SELECT * FROM purchase_orders WHERE po_id = ?", (po_id,)).fetchone()
    if po is None:
        raise DomainError(f"Unknown purchase order: {po_id}", po_id=po_id)
    if po["status"] == "received":
        raise DomainError(f"{po_id} is already fully received", po_id=po_id)

    lines = conn.execute(
        "SELECT * FROM purchase_order_lines WHERE po_id = ? ORDER BY line_no", (po_id,)
    ).fetchall()
    outstanding = {l["sku"]: l["qty_ordered"] - l["qty_received"] for l in lines}

    if receipts is None:
        receipts = [
            {"sku": sku, "quantity": qty} for sku, qty in outstanding.items() if qty > 0
        ]
    else:
        receipts = [
            {"sku": catalog.resolve_sku(conn, r["sku"]), "quantity": r["quantity"]}
            for r in receipts
        ]

    # Validate the whole delivery before applying any of it.
    for receipt in receipts:
        sku, qty = receipt["sku"], int(receipt["quantity"])
        if sku not in outstanding:
            raise DomainError(f"{po_id} has no line for SKU {sku}", po_id=po_id, sku=sku)
        if qty <= 0:
            raise DomainError("quantity must be positive", sku=sku, quantity=qty)
        if qty > outstanding[sku]:
            raise DomainError(
                f"Only {outstanding[sku]} unit(s) of {sku} are outstanding on {po_id}",
                sku=sku,
                outstanding=outstanding[sku],
                requested=qty,
            )

    received = []
    for receipt in receipts:
        sku, qty = receipt["sku"], int(receipt["quantity"])
        conn.execute(
            "UPDATE purchase_order_lines SET qty_received = qty_received + ? WHERE po_id = ? AND sku = ?",
            (qty, po_id, sku),
        )
        conn.execute(
            "UPDATE inventory SET on_hand_qty = on_hand_qty + ? WHERE sku = ?", (qty, sku)
        )
        received.append({"sku": sku, "quantity": qty})

    fully_received = all(
        l["qty_received"] == l["qty_ordered"]
        for l in conn.execute(
            "SELECT qty_ordered, qty_received FROM purchase_order_lines WHERE po_id = ?",
            (po_id,),
        )
    )
    if fully_received:
        conn.execute(
            "UPDATE purchase_orders SET status = 'received' WHERE po_id = ?", (po_id,)
        )
    conn.commit()

    still_outstanding = [
        {"sku": l["sku"], "outstanding": l["qty_ordered"] - l["qty_received"]}
        for l in conn.execute(
            "SELECT * FROM purchase_order_lines WHERE po_id = ? AND qty_received < qty_ordered",
            (po_id,),
        )
    ]
    return {
        "po_id": po_id,
        "received_date": received_date,
        "received": received,
        "status": "received" if fully_received else "open",
        "still_outstanding": still_outstanding,
    }
