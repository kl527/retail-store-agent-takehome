"""Rules 2 and 5: proration/rounding and promotion windows."""

from decimal import Decimal

import pytest

from store_agent.domain.pricing import create_promotion, effective_unit_price
from store_agent.errors import DomainError
from store_agent.money import discounted_unit_price


def test_promo_window_inclusive_edges(conn):
    # Spring Tee Sale: 20% off P-TEE, 2026-05-01..2026-05-07 inclusive.
    for date, expected in [
        ("2026-04-30", Decimal("25.00")),
        ("2026-05-01", Decimal("20.00")),
        ("2026-05-07", Decimal("20.00")),
        ("2026-05-08", Decimal("25.00")),
    ]:
        price, _ = effective_unit_price(conn, "TEE-BLU-M", date)
        assert price == expected, date


def test_overlapping_promos_lower_price_wins_no_stacking(conn):
    create_promotion(conn, "10% apparel", 10, "category", "apparel", "2026-06-20", "2026-06-22")
    create_promotion(conn, "20% hoodies", 20, "product", "P-HOOD", "2026-06-20", "2026-06-22")
    price, promo = effective_unit_price(conn, "HOOD-GRY-M", "2026-06-21")
    assert price == Decimal("48.00")  # 20% wins; stacking would give 43.20
    assert promo == "PR-003"


def test_prompt8_hoodie_promo_pricing(conn):
    create_promotion(conn, "Hoodie sale", 20, "product", "P-HOOD", "2026-06-20", "2026-06-22")
    assert effective_unit_price(conn, "HOOD-GRY-M", "2026-06-21")[0] == Decimal("48.00")
    assert effective_unit_price(conn, "HOOD-GRY-M", "2026-06-23")[0] == Decimal("60.00")


def test_order_discount_proration_half_up(conn):
    # The documented O-1006 examples.
    assert discounted_unit_price("60.00", 10) == Decimal("54.00")
    assert discounted_unit_price("18.00", 10) == Decimal("16.20")
    # Half-up at the third decimal: 9.99 * 0.75 = 7.4925 -> 7.49; 9.90 * 0.75 = 7.425 -> 7.43
    assert discounted_unit_price("9.99", 25) == Decimal("7.49")
    assert discounted_unit_price("9.90", 25) == Decimal("7.43")


def test_create_promotion_validation(conn):
    with pytest.raises(DomainError):
        create_promotion(conn, "bad scope", 10, "product", "P-NOPE", "2026-06-20", "2026-06-21")
    with pytest.raises(DomainError):
        create_promotion(conn, "bad dates", 10, "category", "goods", "2026-06-22", "2026-06-20")
    with pytest.raises(DomainError):
        create_promotion(conn, "bad pct", 0, "category", "goods", "2026-06-20", "2026-06-21")
