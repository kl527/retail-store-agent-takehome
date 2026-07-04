"""Schema + seed loader.

The DDL below IS the domain model (docs/domain-model.md walks through it).
Every session starts from a fresh in-memory database rebuilt from data/, so
the store always begins in the documented 2026-06-19 state.

purchase_orders / purchase_order_lines are invented entities: the seed has no
PO export, but the restock and receiving workflows require them.
"""

import csv
import re
import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE products (
    sku           TEXT PRIMARY KEY,
    product_id    TEXT NOT NULL,
    product_name  TEXT NOT NULL,
    category      TEXT NOT NULL CHECK (category IN ('apparel', 'goods')),
    color         TEXT,               -- NULL when the product has no variants
    size          TEXT,
    retail_price  REAL NOT NULL
);

CREATE TABLE customers (
    customer_id  TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    email        TEXT NOT NULL,
    joined_date  TEXT NOT NULL
);

CREATE TABLE suppliers (
    supplier_id    TEXT PRIMARY KEY,
    supplier_name  TEXT NOT NULL
);

CREATE TABLE supplier_catalog (
    supplier_id     TEXT NOT NULL REFERENCES suppliers(supplier_id),
    product_id      TEXT NOT NULL,
    unit_cost       REAL NOT NULL,
    lead_time_days  INTEGER NOT NULL,
    PRIMARY KEY (supplier_id, product_id)
);

CREATE TABLE inventory (
    sku            TEXT PRIMARY KEY REFERENCES products(sku),
    on_hand_qty    INTEGER NOT NULL CHECK (on_hand_qty >= 0),
    reorder_point  INTEGER NOT NULL,
    reorder_qty    INTEGER NOT NULL
);

CREATE TABLE orders (
    order_id            TEXT PRIMARY KEY,
    order_date          TEXT NOT NULL,
    customer_id         TEXT REFERENCES customers(customer_id),  -- NULL = walk-in
    order_discount_pct  REAL NOT NULL DEFAULT 0,
    payment_method      TEXT NOT NULL CHECK (payment_method IN ('cash', 'card'))
);

CREATE TABLE order_lines (
    order_id    TEXT NOT NULL REFERENCES orders(order_id),
    line_no     INTEGER NOT NULL,
    sku         TEXT NOT NULL REFERENCES products(sku),
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    -- Per-unit price charged that day (item-level promos already applied),
    -- BEFORE the order-level discount.
    unit_price  REAL NOT NULL,
    PRIMARY KEY (order_id, line_no)
);

CREATE TABLE returns (
    return_id      TEXT PRIMARY KEY,
    return_date    TEXT NOT NULL,
    order_id       TEXT NOT NULL REFERENCES orders(order_id),
    sku            TEXT NOT NULL REFERENCES products(sku),
    quantity       INTEGER NOT NULL CHECK (quantity > 0),
    condition      TEXT NOT NULL CHECK (condition IN ('good', 'damaged')),
    refund_amount  REAL NOT NULL
);

CREATE TABLE promotions (
    promo_id     TEXT PRIMARY KEY,
    description  TEXT NOT NULL,
    type         TEXT NOT NULL CHECK (type = 'percent_off'),
    value        REAL NOT NULL,
    scope_type   TEXT NOT NULL CHECK (scope_type IN ('product', 'category')),
    scope_ref    TEXT NOT NULL,
    start_date   TEXT NOT NULL,       -- inclusive
    end_date     TEXT NOT NULL        -- inclusive
);

CREATE TABLE purchase_orders (
    po_id         TEXT PRIMARY KEY,
    supplier_id   TEXT NOT NULL REFERENCES suppliers(supplier_id),
    created_date  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'received'))
);

CREATE TABLE purchase_order_lines (
    po_id         TEXT NOT NULL REFERENCES purchase_orders(po_id),
    line_no       INTEGER NOT NULL,
    sku           TEXT NOT NULL REFERENCES products(sku),
    qty_ordered   INTEGER NOT NULL CHECK (qty_ordered > 0),
    qty_received  INTEGER NOT NULL DEFAULT 0,
    unit_cost     REAL NOT NULL,
    PRIMARY KEY (po_id, line_no)
);
"""

# csv file -> (table, columns that become NULL when blank, integer columns)
_LOADS = [
    ("products.csv", "products", {"color", "size"}, set()),
    ("customers.csv", "customers", set(), set()),
    ("suppliers.csv", "suppliers", set(), set()),
    ("supplier_catalog.csv", "supplier_catalog", set(), {"lead_time_days"}),
    ("inventory.csv", "inventory", set(), {"on_hand_qty", "reorder_point", "reorder_qty"}),
    ("orders.csv", "orders", {"customer_id"}, set()),
    ("order_lines.csv", "order_lines", set(), {"line_no", "quantity"}),
    ("returns.csv", "returns", set(), {"quantity"}),
    ("promotions.csv", "promotions", set(), set()),
]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def load_seed(conn: sqlite3.Connection, data_dir: Path) -> None:
    conn.executescript(SCHEMA)
    for filename, table, nullable, int_cols in _LOADS:
        with open(data_dir / filename, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cols, vals = [], []
                for col, raw in row.items():
                    cols.append(col)
                    if raw == "" and col in nullable:
                        vals.append(None)
                    elif col in int_cols:
                        vals.append(int(raw))
                    else:
                        vals.append(raw)
                placeholders = ", ".join("?" for _ in cols)
                conn.execute(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
                    vals,
                )
    conn.commit()


def fresh_store(data_dir: Path | None = None) -> sqlite3.Connection:
    from .config import data_dir as default_data_dir

    conn = connect()
    load_seed(conn, data_dir or default_data_dir())
    return conn


def next_id(conn: sqlite3.Connection, table: str, column: str, prefix: str, start: int, pad: int = 0) -> str:
    """Continue the seed's ID sequences: O-1016, R-2002, PR-002, PO-3001..."""
    highest = start - 1
    for (value,) in conn.execute(f"SELECT {column} FROM {table}"):
        m = re.fullmatch(re.escape(prefix) + r"(\d+)", value)
        if m:
            highest = max(highest, int(m.group(1)))
    return f"{prefix}{highest + 1:0{pad}d}"


def next_order_id(conn):
    return next_id(conn, "orders", "order_id", "O-", 1001)


def next_return_id(conn):
    return next_id(conn, "returns", "return_id", "R-", 2001)


def next_po_id(conn):
    return next_id(conn, "purchase_orders", "po_id", "PO-", 3001)


def next_promo_id(conn):
    return next_id(conn, "promotions", "promo_id", "PR-", 1, pad=3)
