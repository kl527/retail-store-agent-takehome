"""Rules 6 and 7: revenue, margin, velocity/stock-out — hand-verified numbers."""

import pytest

from store_agent.domain.reports import revenue_report, stockout_report, top_products_by_margin
from store_agent.errors import DomainError


def test_may_revenue(conn):
    report = revenue_report(conn, "2026-05-01", "2026-05-31")
    # Sum of all May lines at paid prices (only O-1006 had a discount): 1786.20.
    assert report["gross_revenue"] == "1786.20"
    assert report["refunds_issued"] == "54.00"  # R-2001
    assert report["net_revenue"] == "1732.20"


def test_prompt9_top_products_by_margin(conn):
    report = top_products_by_margin(conn, "2026-05-01", "2026-05-31", limit=5)
    rows = [(p["product_id"], p["margin"]) for p in report["products"]]
    assert rows == [
        ("P-TEE", "420.00"),   # rev 720 (30 units) − cost 300
        ("P-HOOD", "282.00"),  # rev 534 (10 sold, 1 restocked @54) − cost 9×28
        ("P-SOCK", "120.00"),  # rev 180 − cost 60
        ("P-TOTE", "108.20"),  # rev 178.20 (incl. 16.20 discounted) − cost 70
        ("P-MUG", "70.00"),    # rev 120 − cost 50
    ]
    hood = next(p for p in report["products"] if p["product_id"] == "P-HOOD")
    assert hood["units_sold"] == 10
    assert hood["units_returned_to_stock"] == 1


def test_reversed_date_range_rejected(conn):
    # A swapped start/end would otherwise silently read as SQL BETWEEN's
    # empty set — "$0.00 revenue" reading as "no May sales" instead of a typo.
    with pytest.raises(DomainError):
        revenue_report(conn, "2026-05-31", "2026-05-01")
    with pytest.raises(DomainError):
        top_products_by_margin(conn, "2026-05-31", "2026-05-01")


def test_prompt10_stockout_flags_only_tote(conn):
    report = stockout_report(conn)
    flagged = [p for p in report["products"] if p["about_to_stock_out"]]
    assert [p["product_id"] for p in flagged] == ["P-TOTE"]
    tote = flagged[0]
    # 10 totes sold in May, 4 on hand -> 12 days of cover; also below reorder point.
    assert tote["days_of_cover"] == 12.0
    assert tote["variants_below_reorder_point"] == ["TOTE"]
    hood = next(p for p in report["products"] if p["product_id"] == "P-HOOD")
    assert hood["days_of_cover"] == 90.0
    assert not hood["about_to_stock_out"]
