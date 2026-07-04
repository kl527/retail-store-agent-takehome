"""Ringing up sales: stock checks, promo pricing, proration."""

import pytest

from store_agent.domain.sales import get_order, ring_up_sale
from store_agent.errors import DomainError


def test_prompt1_two_tees_and_a_tote(conn):
    result = ring_up_sale(
        conn,
        [{"sku": "TEE-BLU-M", "quantity": 2}, {"sku": "TOTE", "quantity": 1}],
        "2026-06-19",
        payment_method="cash",
    )
    assert result["order_id"] == "O-1016"
    assert result["customer_id"] is None  # walk-in
    assert result["order_total"] == "68.00"  # 2×25 + 18, no promo active today
    assert conn.execute("SELECT on_hand_qty FROM inventory WHERE sku='TEE-BLU-M'").fetchone()[0] == 20
    assert conn.execute("SELECT on_hand_qty FROM inventory WHERE sku='TOTE'").fetchone()[0] == 3


def test_prompt2_insufficient_stock_refuses_whole_sale(conn):
    with pytest.raises(DomainError) as exc:
        ring_up_sale(conn, [{"sku": "TOTE", "quantity": 10}], "2026-06-19")
    assert exc.value.details["shortages"] == [{"sku": "TOTE", "requested": 10, "on_hand": 4}]
    # Nothing was recorded or decremented.
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 15
    assert conn.execute("SELECT on_hand_qty FROM inventory WHERE sku='TOTE'").fetchone()[0] == 4


def test_order_discount_prorates_each_line(conn):
    result = ring_up_sale(
        conn,
        [{"sku": "HOOD-NVY-L", "quantity": 1}, {"sku": "TOTE", "quantity": 1}],
        "2026-06-19",
        customer_id="C-001",
        order_discount_pct=10,
        payment_method="card",
    )
    by_sku = {l["sku"]: l for l in result["lines"]}
    assert by_sku["HOOD-NVY-L"]["paid_unit_price"] == "54.00"
    assert by_sku["TOTE"]["paid_unit_price"] == "16.20"
    assert result["order_total"] == "70.20"


def test_sale_on_promo_date_uses_promo_price(conn):
    from store_agent.domain.pricing import create_promotion

    create_promotion(conn, "Hoodie sale", 20, "product", "P-HOOD", "2026-06-20", "2026-06-22")
    result = ring_up_sale(conn, [{"sku": "HOOD-GRY-M", "quantity": 1}], "2026-06-21")
    line = result["lines"][0]
    assert line["unit_price"] == "48.00"
    assert line["promo_applied"] == "PR-002"
    assert result["order_total"] == "48.00"


def test_duplicate_skus_merge_into_one_line(conn):
    result = ring_up_sale(
        conn,
        [{"sku": "MUG", "quantity": 1}, {"sku": "MUG", "quantity": 2}],
        "2026-06-19",
    )
    assert len(result["lines"]) == 1
    assert result["lines"][0]["quantity"] == 3


def test_lenient_sku_resolution(conn):
    # Product names and product_ids resolve when unambiguous...
    result = ring_up_sale(conn, [{"sku": "Canvas Tote", "quantity": 1}], "2026-06-19")
    assert result["lines"][0]["sku"] == "TOTE"
    result = ring_up_sale(conn, [{"sku": "P-MUG", "quantity": 1}], "2026-06-19")
    assert result["lines"][0]["sku"] == "MUG"
    # ...but a multi-variant reference errors, listing the candidates.
    with pytest.raises(DomainError) as exc:
        ring_up_sale(conn, [{"sku": "P-TEE", "quantity": 1}], "2026-06-19")
    assert len(exc.value.details["candidates"]) == 6


def test_validation_errors(conn):
    with pytest.raises(DomainError):
        ring_up_sale(conn, [{"sku": "NOPE", "quantity": 1}], "2026-06-19")
    with pytest.raises(DomainError):
        ring_up_sale(conn, [{"sku": "MUG", "quantity": 1}], "2026-06-19", customer_id="C-999")
    with pytest.raises(DomainError):
        ring_up_sale(conn, [], "2026-06-19")
    with pytest.raises(DomainError):
        ring_up_sale(conn, [{"sku": "MUG", "quantity": 0}], "2026-06-19")


def test_get_order_includes_paid_prices_and_returns(conn):
    order = get_order(conn, "O-1006")
    by_sku = {l["sku"]: l for l in order["lines"]}
    assert by_sku["HOOD-NVY-L"]["unit_price"] == "60.00"
    assert by_sku["HOOD-NVY-L"]["paid_unit_price"] == "54.00"
    assert by_sku["TOTE"]["paid_unit_price"] == "16.20"
    assert len(order["returns"]) == 1
    assert order["returns"][0]["return_id"] == "R-2001"
