"""The tool layer: dispatch, structured errors, and the SQL sandbox."""

from store_agent import tools


def test_every_tool_has_valid_openai_spec():
    specs = tools.openai_tool_specs()
    assert len(specs) == 17
    for spec in specs:
        fn = spec["function"]
        assert fn["name"] and fn["description"]
        assert fn["parameters"]["type"] == "object"


def test_dispatch_happy_path(conn):
    result = tools.dispatch(conn, "search_products", {"query": "canvas tote"})
    assert result["matches"][0]["product_id"] == "P-TOTE"
    result = tools.dispatch(conn, "get_price_quote", {"sku": "TOTE"})
    assert result["unit_price"] == "18.00"


def test_dispatch_wraps_domain_errors(conn):
    result = tools.dispatch(
        conn, "ring_up_sale", {"items": [{"sku": "TOTE", "quantity": 10}], "date": "2026-06-19"}
    )
    assert "error" in result
    assert result["details"]["shortages"][0]["on_hand"] == 4


def test_dispatch_rejects_unknown_tool_and_bad_args(conn):
    assert "error" in tools.dispatch(conn, "no_such_tool", {})
    assert "error" in tools.dispatch(conn, "search_products", {"nope": 1})


def test_run_sql_is_read_only(conn):
    ok = tools.dispatch(conn, "run_sql", {"query": "SELECT COUNT(*) AS n FROM products"})
    assert ok["rows"] == [[13]]
    for evil in [
        "DELETE FROM orders",
        "INSERT INTO suppliers VALUES ('X','X')",
        "WITH x AS (SELECT 1) INSERT INTO suppliers VALUES ('X','X')",
    ]:
        result = tools.dispatch(conn, "run_sql", {"query": evil})
        assert "error" in result, evil
    # State unchanged and connection still writable afterwards.
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 15
    conn.execute("UPDATE inventory SET on_hand_qty = on_hand_qty WHERE sku = 'TOTE'")


def test_markdown_reference_lists_all_tools():
    doc = tools.markdown_reference()
    for spec in tools.openai_tool_specs():
        assert f"`{spec['function']['name']}`" in doc
