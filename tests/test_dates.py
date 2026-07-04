"""Calendar-date validation: dates are opaque TEXT to SQLite, so nothing
else catches a syntactically-plausible but nonexistent date."""

import pytest

from store_agent.domain.dates import validate_date
from store_agent.domain.pricing import create_promotion
from store_agent.domain.purchasing import create_purchase_order
from store_agent.domain.returns import process_return
from store_agent.domain.sales import ring_up_sale
from store_agent.errors import DomainError


def test_validate_date_rejects_nonexistent_calendar_date():
    with pytest.raises(DomainError):
        validate_date("2026-02-30")
    with pytest.raises(DomainError):
        validate_date("not-a-date")
    assert validate_date("2026-06-19") == "2026-06-19"


def test_ring_up_sale_rejects_invalid_date(conn):
    with pytest.raises(DomainError):
        ring_up_sale(conn, [{"sku": "TOTE", "quantity": 1}], "2026-02-30")
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 15


def test_process_return_rejects_invalid_date(conn):
    with pytest.raises(DomainError):
        process_return(conn, "O-1006", "TOTE", 1, "good", "2026-13-01")
    assert conn.execute("SELECT COUNT(*) FROM returns").fetchone()[0] == 1


def test_create_purchase_order_rejects_invalid_date(conn):
    with pytest.raises(DomainError):
        create_purchase_order(conn, "SUP-NW", [{"sku": "TOTE", "quantity": 10}], "2026-04-31")
    assert conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0] == 0


def test_create_promotion_rejects_invalid_start_or_end_date(conn):
    with pytest.raises(DomainError):
        create_promotion(conn, "Bad promo", 20, "product", "P-HOOD", "2026-06-31", "2026-07-05")
    with pytest.raises(DomainError):
        create_promotion(conn, "Bad promo", 20, "product", "P-HOOD", "2026-06-20", "2026-06-31")
    assert conn.execute("SELECT COUNT(*) FROM promotions").fetchone()[0] == 1
