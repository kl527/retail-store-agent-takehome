from store_agent import db


def test_seed_row_counts(conn):
    counts = {
        "products": 13,
        "customers": 4,
        "suppliers": 2,
        "supplier_catalog": 7,
        "inventory": 13,
        "orders": 15,
        "order_lines": 22,
        "returns": 1,
        "promotions": 1,
        "purchase_orders": 0,
    }
    for table, expected in counts.items():
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == expected


def test_blanks_become_null(conn):
    walk_in = conn.execute("SELECT customer_id FROM orders WHERE order_id = 'O-1002'").fetchone()
    assert walk_in["customer_id"] is None
    tote = conn.execute("SELECT color, size FROM products WHERE sku = 'TOTE'").fetchone()
    assert tote["color"] is None and tote["size"] is None


def test_id_sequences_continue_seed(conn):
    assert db.next_order_id(conn) == "O-1016"
    assert db.next_return_id(conn) == "R-2002"
    assert db.next_po_id(conn) == "PO-3001"
    assert db.next_promo_id(conn) == "PR-002"
