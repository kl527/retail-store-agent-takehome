"""Rule 3: refund the price actually paid; restock only good condition."""

import pytest

from store_agent.domain.returns import process_return
from store_agent.errors import DomainError


def test_prompt6_good_hoodie_return_restocks(conn):
    result = process_return(conn, "O-1006", "HOOD-NVY-L", 1, "good", "2026-06-19")
    assert result["return_id"] == "R-2002"
    assert result["refund_amount"] == "54.00"  # paid price, not the 60.00 list
    assert result["restocked"] is True
    assert conn.execute("SELECT on_hand_qty FROM inventory WHERE sku='HOOD-NVY-L'").fetchone()[0] == 7


def test_prompt7_damaged_tote_no_restock(conn):
    result = process_return(conn, "O-1006", "TOTE", 1, "damaged", "2026-06-19")
    assert result["refund_amount"] == "16.20"
    assert result["restocked"] is False
    assert conn.execute("SELECT on_hand_qty FROM inventory WHERE sku='TOTE'").fetchone()[0] == 4


def test_cannot_return_more_than_remaining(conn):
    # O-1006 sold 2 Navy-L hoodies; seed already returned 1 (R-2001).
    with pytest.raises(DomainError) as exc:
        process_return(conn, "O-1006", "HOOD-NVY-L", 2, "good", "2026-06-19")
    assert exc.value.details == {"sold": 2, "already_returned": 1, "requested": 2}
    # Returning the one remaining unit works; a second attempt then fails.
    process_return(conn, "O-1006", "HOOD-NVY-L", 1, "good", "2026-06-19")
    with pytest.raises(DomainError):
        process_return(conn, "O-1006", "HOOD-NVY-L", 1, "good", "2026-06-19")


def test_validation_errors(conn):
    with pytest.raises(DomainError):
        process_return(conn, "O-9999", "TOTE", 1, "good", "2026-06-19")
    with pytest.raises(DomainError):
        process_return(conn, "O-1006", "MUG", 1, "good", "2026-06-19")  # not on that order
    with pytest.raises(DomainError):
        process_return(conn, "O-1006", "TOTE", 1, "shredded", "2026-06-19")
