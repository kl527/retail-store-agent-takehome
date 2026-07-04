"""The tool/action layer the agent exposes (deliverable 2).

Each entry pairs an OpenAI-format function schema with a handler. The LLM
never computes store math — every number it reports comes out of these tools.
Domain errors come back as {"error": ..., "details": ...} so the model can
explain the problem instead of crashing or guessing.

`python -m store_agent.tools` prints the reference table used in the docs.
"""

import json
import sqlite3

from .config import LAST_MONTH_END, LAST_MONTH_START, TODAY
from .domain import catalog, pricing, purchasing, reports, returns, sales
from .errors import DomainError
from .money import money_str

_ITEMS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "sku": {"type": "string"},
            "quantity": {"type": "integer", "minimum": 1},
        },
        "required": ["sku", "quantity"],
    },
}

_DATE = {"type": "string", "description": "YYYY-MM-DD"}


def _search_products(conn, query, color=None, size=None):
    results = catalog.search_products(conn, query, color=color, size=size)
    if not results:
        return {"matches": [], "hint": "no product matched; try fewer words"}
    return {"matches": results}


def _lookup_customer(conn, query):
    matches = catalog.lookup_customer(conn, query)
    if not matches:
        return {"matches": [], "hint": "no customer matched; sales can also be walk-in (no customer)"}
    return {"matches": matches}


def _get_price_quote(conn, sku, date=TODAY):
    price, promo_id = pricing.effective_unit_price(conn, sku, date)
    return {
        "sku": sku,
        "date": date,
        "unit_price": money_str(price),
        "promo_applied": promo_id,
    }


def _simulate_discount_price(conn, sku, percent_off):
    price = pricing.simulate_discount_price(conn, sku, percent_off)
    return {"sku": sku, "percent_off": percent_off, "hypothetical_price": money_str(price)}


def _run_sql(conn, query):
    stripped = query.strip().rstrip(";").strip()
    if not stripped.lower().startswith(("select", "with")):
        raise DomainError("Only SELECT queries are allowed", query=query)
    conn.execute("PRAGMA query_only = ON")
    try:
        cursor = conn.execute(stripped)
        columns = [c[0] for c in cursor.description]
        rows = cursor.fetchmany(101)
    except sqlite3.Error as e:
        raise DomainError(f"SQL error: {e}", query=query)
    finally:
        conn.execute("PRAGMA query_only = OFF")
    truncated = len(rows) > 100
    return {
        "columns": columns,
        "rows": [list(r) for r in rows[:100]],
        "truncated": truncated,
    }


TOOLS: list[dict] = [
    {
        "handler": _search_products,
        "schema": {
            "name": "search_products",
            "description": (
                "Resolve a product description to concrete SKUs with stock and list price. "
                "Always call this before selling, returning, restocking, or discounting a product. "
                "If more than one variant matches and the user did not fully specify, ask the user "
                "which one they mean instead of picking."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "e.g. 'hoodie', 'blue tee', 'canvas tote'"},
                    "color": {"type": "string", "description": "optional exact color filter"},
                    "size": {"type": "string", "description": "optional size: S/M/L or small/medium/large"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "handler": _lookup_customer,
        "schema": {
            "name": "lookup_customer",
            "description": (
                "Find a customer by (partial) name, email, or id. Sales without a customer are "
                "walk-ins — never attach a guessed customer."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "handler": lambda conn, **kw: sales.ring_up_sale(
            conn,
            kw["items"],
            kw.get("date", TODAY),
            customer_id=kw.get("customer_id"),
            order_discount_pct=kw.get("order_discount_pct", 0),
            payment_method=kw.get("payment_method", "cash"),
        ),
        "schema": {
            "name": "ring_up_sale",
            "description": (
                "Record a sale — required whenever the user says ring up / sell / record, even if "
                "they also ask for the price (the result includes it). Checks stock (refuses the "
                "whole sale if any item is short), applies any active promotion for the sale date, "
                "decrements inventory, and returns the authoritative prices and order total. Omit "
                "customer_id for walk-ins."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": _ITEMS_SCHEMA,
                    "date": _DATE,
                    "customer_id": {"type": "string"},
                    "order_discount_pct": {
                        "type": "number",
                        "description": "whole-order % discount, 0-100, default 0",
                        "minimum": 0,
                        "maximum": 100,
                    },
                    "payment_method": {"type": "string", "enum": ["cash", "card"]},
                },
                "required": ["items", "date"],
            },
        },
    },
    {
        "handler": lambda conn, **kw: sales.get_order(conn, kw["order_id"]),
        "schema": {
            "name": "get_order",
            "description": (
                "Fetch one order with its lines, per-unit prices actually paid (after the order-level "
                "discount), and any returns already recorded against it. Use before processing a return."
            ),
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        },
    },
    {
        "handler": lambda conn, **kw: sales.list_orders(
            conn,
            customer_id=kw.get("customer_id"),
            start_date=kw.get("start_date"),
            end_date=kw.get("end_date"),
        ),
        "schema": {
            "name": "list_orders",
            "description": "List orders, optionally filtered by customer and/or date range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string"},
                    "start_date": _DATE,
                    "end_date": _DATE,
                },
            },
        },
    },
    {
        "handler": lambda conn, **kw: returns.process_return(
            conn,
            kw["order_id"],
            kw["sku"],
            kw["quantity"],
            kw["condition"],
            kw.get("date", TODAY),
        ),
        "schema": {
            "name": "process_return",
            "description": (
                "Return units from a past order. Refunds the price actually paid on that order "
                "(never the current list price). condition='good' restocks the units; 'damaged' "
                "does not — this materially changes the outcome, so if the user hasn't stated the "
                "condition, ask before calling this tool; never default to 'good'. Rejects "
                "returning more than was sold minus already returned. Pass the quantity the user "
                "asked for — if it exceeds what remains returnable, do NOT process a smaller "
                "quantity instead; report the limit and ask the user first. For a bulk 'return "
                "everything from order X' request, call get_order first, then call this tool once "
                "per line using the skus and quantities it shows — that's a complete, unambiguous "
                "spec, so proceed without asking again (still ask about condition if unstated)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "sku": {"type": "string"},
                    "quantity": {"type": "integer", "minimum": 1},
                    "condition": {"type": "string", "enum": ["good", "damaged"]},
                    "date": _DATE,
                },
                "required": ["order_id", "sku", "quantity", "condition"],
            },
        },
    },
    {
        "handler": lambda conn, **kw: purchasing.restock_below_reorder(conn, kw.get("date", TODAY)),
        "schema": {
            "name": "restock_below_reorder",
            "description": (
                "Scan all inventory and create purchase orders (one per supplier) for every SKU at or "
                "below its reorder point, ordering its reorder quantity from the cheapest supplier "
                "that delivers within 10 days. Returns the POs plus the per-SKU supplier decisions."
            ),
            "parameters": {
                "type": "object",
                "properties": {"date": _DATE},
            },
        },
    },
    {
        "handler": lambda conn, **kw: purchasing.create_purchase_order(
            conn, kw["supplier_id"], kw["items"], kw.get("date", TODAY)
        ),
        "schema": {
            "name": "create_purchase_order",
            "description": (
                "Create a purchase order for specific items from a specific supplier (use "
                "restock_below_reorder for rule-based restocking). supplier_id accepts an id "
                "('SUP-NW') or an unambiguous supplier name ('Northwind'). Unit costs come from "
                "the supplier's catalog. If the user describes a PO as if it already exists (e.g. "
                "'a PO for 50 totes from Northwind is open') and list_purchase_orders shows no such "
                "PO, call this tool immediately with exactly the details given, then continue (e.g. "
                "receive against it) — do not stop to ask the user to confirm creating it first; "
                "they already told you what to create."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "supplier_id": {"type": "string"},
                    "items": _ITEMS_SCHEMA,
                    "date": _DATE,
                },
                "required": ["supplier_id", "items"],
            },
        },
    },
    {
        "handler": lambda conn, **kw: purchasing.list_purchase_orders(conn, status=kw.get("status")),
        "schema": {
            "name": "list_purchase_orders",
            "description": "List purchase orders with their lines and received quantities. Filter by status 'open' or 'received'.",
            "parameters": {
                "type": "object",
                "properties": {"status": {"type": "string", "enum": ["open", "received"]}},
            },
        },
    },
    {
        "handler": lambda conn, **kw: purchasing.receive_purchase_order(
            conn, kw["po_id"], kw.get("date", TODAY), receipts=kw.get("receipts")
        ),
        "schema": {
            "name": "receive_purchase_order",
            "description": (
                "Book a delivery against an open purchase order: adds the units to inventory. "
                "Pass receipts for a partial delivery (e.g. 40 of 50 arrived); omit receipts to "
                "receive everything outstanding. The PO closes automatically when complete. If the "
                "user's phrasing doesn't identify which PO (e.g. 'the shipment arrived' while more "
                "than one PO is open), call list_purchase_orders and ask which one — do not guess "
                "by receiving every open PO unless the user clearly means all of them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "po_id": {"type": "string"},
                    "receipts": _ITEMS_SCHEMA,
                    "date": _DATE,
                },
                "required": ["po_id"],
            },
        },
    },
    {
        "handler": lambda conn, **kw: pricing.create_promotion(
            conn,
            kw["description"],
            kw["percent_off"],
            kw["scope_type"],
            kw["scope_ref"],
            kw["start_date"],
            kw["end_date"],
        ),
        "schema": {
            "name": "create_promotion",
            "description": (
                "Create a REAL, persistent percent-off promotion. Only call this when the user is "
                "actually instructing you to set up or apply a promotion. Never call it just to "
                "answer a hypothetical 'what would X cost if...' question — use "
                "simulate_discount_price for those instead. scope_type='product' with scope_ref = "
                "product_id covers ALL variants of one product — e.g. 'all hoodies' means P-HOOD, "
                "NOT the apparel category. Use scope_type='category' (scope_ref 'apparel' or "
                "'goods') only when the user names a whole category. Start/end dates are inclusive. "
                "Promotions never change past sales; overlapping promotions don't stack — the lower "
                "price wins."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "percent_off": {"type": "number"},
                    "scope_type": {"type": "string", "enum": ["product", "category"]},
                    "scope_ref": {"type": "string"},
                    "start_date": _DATE,
                    "end_date": _DATE,
                },
                "required": ["description", "percent_off", "scope_type", "scope_ref", "start_date", "end_date"],
            },
        },
    },
    {
        "handler": _get_price_quote,
        "schema": {
            "name": "get_price_quote",
            "description": (
                "The effective per-unit price of a SKU on a given date, with any promotion applied. "
                "ONLY for hypothetical 'what would X cost' questions — it records nothing. If the "
                "user asked to ring up / sell / record a sale, use ring_up_sale instead; its result "
                "already includes the price to report."
            ),
            "parameters": {
                "type": "object",
                "properties": {"sku": {"type": "string"}, "date": _DATE},
                "required": ["sku"],
            },
        },
    },
    {
        "handler": _simulate_discount_price,
        "schema": {
            "name": "simulate_discount_price",
            "description": (
                "Compute a HYPOTHETICAL price for a SKU under an arbitrary percent-off, without "
                "creating or changing anything — records nothing. Use this for 'what would X cost "
                "if we put it on Y% off' questions. It answers only the hypothetical asked (it does "
                "not layer on top of other active promotions). To actually set up a discount for "
                "real, use create_promotion instead — never call create_promotion just to answer a "
                "hypothetical."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sku": {"type": "string"},
                    "percent_off": {"type": "number"},
                },
                "required": ["sku", "percent_off"],
            },
        },
    },
    {
        "handler": lambda conn, **kw: reports.revenue_report(
            conn, kw.get("start_date", LAST_MONTH_START), kw.get("end_date", LAST_MONTH_END)
        ),
        "schema": {
            "name": "revenue_report",
            "description": (
                "Gross revenue (dollars actually paid on orders in the period), refunds issued in "
                "the period, and net revenue. Defaults to last month (May 2026)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"start_date": _DATE, "end_date": _DATE},
            },
        },
    },
    {
        "handler": lambda conn, **kw: reports.top_products_by_margin(
            conn,
            kw.get("start_date", LAST_MONTH_START),
            kw.get("end_date", LAST_MONTH_END),
            limit=kw.get("limit", 5),
        ),
        "schema": {
            "name": "top_products_by_margin",
            "description": (
                "Products ranked by margin dollars for a period (margin % included). Margin follows "
                "the store rule: revenue from a product's units minus Northwind cost of units that "
                "stayed sold; restocked returns count in neither. Defaults to last month (May 2026)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"start_date": _DATE, "end_date": _DATE, "limit": {"type": "integer"}},
            },
        },
    },
    {
        "handler": lambda conn, **kw: reports.stockout_report(conn),
        "schema": {
            "name": "stockout_report",
            "description": (
                "Which products are about to stock out: any variant at/below its reorder point, or "
                "fewer than 14 days of cover at last-30-days sales velocity."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "handler": _run_sql,
        "schema": {
            "name": "run_sql",
            "description": (
                "Read-only SELECT against the store database for questions no other tool answers. "
                "Tables: products(sku, product_id, product_name, category, color, size, retail_price), "
                "customers, suppliers, supplier_catalog(supplier_id, product_id, unit_cost, lead_time_days), "
                "inventory(sku, on_hand_qty, reorder_point, reorder_qty), "
                "orders(order_id, order_date, customer_id, order_discount_pct, payment_method), "
                "order_lines(order_id, line_no, sku, quantity, unit_price), "
                "returns, promotions, purchase_orders, purchase_order_lines. "
                "Prefer the report tools for revenue/margin/stock-out — they encode the store's "
                "frozen business rules."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]

_HANDLERS = {t["schema"]["name"]: t["handler"] for t in TOOLS}


def openai_tool_specs() -> list[dict]:
    return [{"type": "function", "function": t["schema"]} for t in TOOLS]


def dispatch(conn: sqlite3.Connection, name: str, arguments: dict) -> dict:
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        result = handler(conn, **arguments)
        return result if isinstance(result, dict) else {"result": result}
    except DomainError as e:
        conn.rollback()  # never leave a failed action half-applied
        return {"error": e.message, "details": e.details}
    except TypeError as e:
        return {"error": f"Bad arguments for {name}: {e}"}


def markdown_reference() -> str:
    """Render the tool table for docs (kept generated so it can't drift)."""
    out = ["| Tool | Parameters | Description |", "|---|---|---|"]
    for t in TOOLS:
        s = t["schema"]
        props = s["parameters"].get("properties", {})
        required = set(s["parameters"].get("required", []))
        params = ", ".join(
            f"`{p}`" + ("" if p in required else "?") for p in props
        ) or "—"
        desc = " ".join(s["description"].split())
        out.append(f"| `{s['name']}` | {params} | {desc} |")
    return "\n".join(out)


if __name__ == "__main__":
    print(markdown_reference())
