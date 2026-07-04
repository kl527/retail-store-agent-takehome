"""Whole-unit quantities: you can't sell, return, order, or receive half an
item — plain int() truncation would silently under-record instead."""

import pytest

from store_agent.domain.purchasing import create_purchase_order, receive_purchase_order
from store_agent.domain.quantities import whole_quantity
from store_agent.domain.returns import process_return
from store_agent.domain.sales import ring_up_sale
from store_agent.errors import DomainError


def test_whole_quantity_rejects_fractional_and_non_numeric():
    with pytest.raises(DomainError):
        whole_quantity(1.5)
    with pytest.raises(DomainError):
        whole_quantity("two")
    assert whole_quantity(3) == 3
    assert whole_quantity("4") == 4  # a numeric string is fine, just not fractional


def test_ring_up_sale_rejects_fractional_quantity(conn):
    with pytest.raises(DomainError):
        ring_up_sale(conn, [{"sku": "HOOD-GRY-M", "quantity": 1.5}], "2026-06-19")
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 15


def test_process_return_rejects_fractional_quantity(conn):
    with pytest.raises(DomainError):
        process_return(conn, "O-1006", "TOTE", 1.5, "good", "2026-06-19")
    assert conn.execute("SELECT COUNT(*) FROM returns").fetchone()[0] == 1


def test_create_purchase_order_rejects_fractional_quantity(conn):
    with pytest.raises(DomainError):
        create_purchase_order(conn, "SUP-NW", [{"sku": "TOTE", "quantity": 2.5}], "2026-06-19")
    assert conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0] == 0


def test_receive_purchase_order_rejects_fractional_quantity(conn):
    po = create_purchase_order(conn, "SUP-NW", [{"sku": "TOTE", "quantity": 10}], "2026-06-19")
    with pytest.raises(DomainError):
        receive_purchase_order(
            conn, po["po_id"], "2026-06-19", receipts=[{"sku": "TOTE", "quantity": 4.5}]
        )
    assert conn.execute("SELECT on_hand_qty FROM inventory WHERE sku='TOTE'").fetchone()[0] == 4
