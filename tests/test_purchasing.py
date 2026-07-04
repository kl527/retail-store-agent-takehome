"""Rule 4 supplier selection + the PO lifecycle (prompts 4 and 5)."""

import pytest

from store_agent.domain.purchasing import (
    create_purchase_order,
    eligible_supplier,
    list_purchase_orders,
    receive_purchase_order,
    restock_below_reorder,
)
from store_agent.errors import DomainError


def test_supplier_selection_rule4(conn):
    # Tote: Pioneer is cheaper (6.50) but 14-day lead makes it ineligible.
    assert eligible_supplier(conn, "P-TOTE")["supplier_id"] == "SUP-NW"
    # Mug: Pioneer is cheaper (4.50) and its 10-day lead is exactly eligible.
    assert eligible_supplier(conn, "P-MUG")["supplier_id"] == "SUP-PG"


def test_prompt4_restock_creates_the_expected_po(conn):
    result = restock_below_reorder(conn, "2026-06-19")
    # Only the tote is at/below its reorder point (4 <= 10).
    assert [d["sku"] for d in result["decisions"]] == ["TOTE"]
    decision = result["decisions"][0]
    assert decision["supplier"] == "Northwind Supply"
    assert decision["ordered_qty"] == 50
    assert decision["unit_cost"] == "7.00"
    assert len(result["purchase_orders"]) == 1
    po = result["purchase_orders"][0]
    assert po["po_id"] == "PO-3001"
    assert po["status"] == "open"
    assert po["lines"] == [
        {"line_no": 1, "sku": "TOTE", "qty_ordered": 50, "qty_received": 0, "unit_cost": "7.00"}
    ]
    # Idempotence guard: PO creation doesn't touch on-hand until receiving.
    assert conn.execute("SELECT on_hand_qty FROM inventory WHERE sku='TOTE'").fetchone()[0] == 4


def test_prompt5_partial_receive_then_complete(conn):
    create_purchase_order(conn, "SUP-NW", [{"sku": "TOTE", "quantity": 50}], "2026-06-19")
    result = receive_purchase_order(
        conn, "PO-3001", "2026-06-19", receipts=[{"sku": "TOTE", "quantity": 40}]
    )
    assert result["status"] == "open"
    assert result["still_outstanding"] == [{"sku": "TOTE", "outstanding": 10}]
    assert conn.execute("SELECT on_hand_qty FROM inventory WHERE sku='TOTE'").fetchone()[0] == 44

    result = receive_purchase_order(conn, "PO-3001", "2026-06-20")  # receive the rest
    assert result["status"] == "received"
    assert conn.execute("SELECT on_hand_qty FROM inventory WHERE sku='TOTE'").fetchone()[0] == 54
    with pytest.raises(DomainError):
        receive_purchase_order(conn, "PO-3001", "2026-06-21")  # already closed


def test_cannot_over_receive(conn):
    create_purchase_order(conn, "SUP-NW", [{"sku": "TOTE", "quantity": 50}], "2026-06-19")
    with pytest.raises(DomainError):
        receive_purchase_order(conn, "PO-3001", "2026-06-19", receipts=[{"sku": "TOTE", "quantity": 60}])
    with pytest.raises(DomainError):
        receive_purchase_order(conn, "PO-3001", "2026-06-19", receipts=[{"sku": "MUG", "quantity": 1}])


def test_supplier_accepts_id_or_unambiguous_name(conn):
    # Small models pass display names as IDs — the tool should cope.
    po = create_purchase_order(conn, "Northwind", [{"sku": "TOTE", "quantity": 5}], "2026-06-19")
    assert po["supplier_id"] == "SUP-NW"
    po = create_purchase_order(conn, "pioneer goods", [{"sku": "MUG", "quantity": 5}], "2026-06-19")
    assert po["supplier_id"] == "SUP-PG"
    with pytest.raises(DomainError) as exc:
        create_purchase_order(conn, "Acme", [{"sku": "TOTE", "quantity": 1}], "2026-06-19")
    # The error teaches the model the valid options.
    assert "SUP-NW (Northwind Supply)" in str(exc.value)


def test_po_validation(conn):
    with pytest.raises(DomainError):
        create_purchase_order(conn, "SUP-XX", [{"sku": "TOTE", "quantity": 1}], "2026-06-19")
    with pytest.raises(DomainError):  # Pioneer doesn't supply tees
        create_purchase_order(conn, "SUP-PG", [{"sku": "TEE-BLU-M", "quantity": 1}], "2026-06-19")
    assert list_purchase_orders(conn) == []
