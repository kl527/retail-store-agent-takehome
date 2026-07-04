"""Fuzzy product/customer resolution.

Matching is deliberately dumb and transparent: case-insensitive token
containment plus size/color synonyms. The LLM sees every candidate and makes
the judgment call (or asks the user) — ranking magic hidden in code would be
harder to explain and debug than candidates surfaced to the model.
"""

import sqlite3

_SIZE_SYNONYMS = {
    "s": "S", "small": "S",
    "m": "M", "medium": "M",
    "l": "L", "large": "L",
}
_COLOR_SYNONYMS = {"grey": "gray"}


def _normalize_size(word: str) -> str | None:
    return _SIZE_SYNONYMS.get(word.lower())


def _normalize_color(word: str) -> str:
    w = word.lower()
    return _COLOR_SYNONYMS.get(w, w)


def _row_matches(row: sqlite3.Row, token: str) -> bool:
    token = _normalize_color(token.lower())
    haystack = " ".join(
        filter(None, [row["product_name"], row["category"], row["color"], row["sku"]])
    ).lower()
    haystack = _normalize_color(haystack).replace("grey", "gray")
    if token in haystack:
        return True
    if token.endswith("s") and token[:-1] in haystack:  # totes -> tote
        return True
    size = _normalize_size(token)
    if size is not None and row["size"] == size:
        return True
    return False


def search_products(
    conn: sqlite3.Connection,
    query: str,
    color: str | None = None,
    size: str | None = None,
) -> list[dict]:
    """Return matching products grouped with their variants and stock."""
    rows = conn.execute(
        """SELECT p.*, i.on_hand_qty FROM products p
           JOIN inventory i ON i.sku = p.sku
           ORDER BY p.product_id, p.sku"""
    ).fetchall()

    tokens = [t for t in query.split() if t]
    matches = []
    for row in rows:
        if not all(_row_matches(row, t) for t in tokens):
            continue
        if color and _normalize_color(color) != (row["color"] or "").lower():
            continue
        if size:
            wanted = _normalize_size(size) or size.upper()
            if (row["size"] or "") != wanted:
                continue
        matches.append(row)

    products: dict[str, dict] = {}
    for row in matches:
        entry = products.setdefault(
            row["product_id"],
            {
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "category": row["category"],
                "variants": [],
            },
        )
        entry["variants"].append(
            {
                "sku": row["sku"],
                "color": row["color"],
                "size": row["size"],
                "retail_price": f"{row['retail_price']:.2f}",
                "on_hand_qty": row["on_hand_qty"],
            }
        )
    return list(products.values())


def resolve_sku(conn: sqlite3.Connection, ref: str) -> str:
    """Accept a SKU, a product_id, or a unique product description.

    Small models pass 'Canvas Tote' or 'P-TOTE' where a SKU belongs; when the
    reference is unambiguous, resolving it beats erroring. Ambiguity (a
    multi-variant product) raises an error that lists the candidate SKUs so
    the model can pick or ask the user.
    """
    from ..errors import DomainError

    if conn.execute("SELECT 1 FROM products WHERE sku = ?", (ref,)).fetchone():
        return ref
    rows = conn.execute("SELECT sku FROM products WHERE product_id = ?", (ref,)).fetchall()
    variants = [r["sku"] for r in rows]
    if not variants:
        variants = [
            v["sku"] for p in search_products(conn, ref) for v in p["variants"]
        ]
    if not variants:
        raise DomainError(
            f"Unknown SKU or product: {ref} — call search_products to find it", ref=ref
        )
    if len(variants) == 1:
        return variants[0]
    raise DomainError(
        f"'{ref}' matches multiple variants: {', '.join(variants)} — "
        "pick one (ask the user if their request doesn't say which)",
        candidates=variants,
    )


def lookup_customer(conn: sqlite3.Connection, query: str) -> list[dict]:
    q = f"%{query.lower()}%"
    rows = conn.execute(
        """SELECT * FROM customers
           WHERE lower(name) LIKE ? OR lower(email) LIKE ? OR lower(customer_id) LIKE ?
           ORDER BY customer_id""",
        (q, q, q),
    ).fetchall()
    return [dict(r) for r in rows]
