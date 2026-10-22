"""
sql_generator.py
------------------
MODULE: Text-to-SQL Generator

Turns a *fully resolved* Intent (no ambiguous slots left -- the pipeline
guarantees the Ambiguity Detector has already approved it) into a SQL
string using the schema knowledge from SchemaManager.

Deliberately NOT a lookup table of "if question == X, return SQL for X".
Instead, generation is driven purely by the Intent's slots (entity, metric,
aggregation, filters, group_by, limit, time_range). This is what lets the
system answer many different phrasings and combinations without new code
per question -- it's the same handful of template branches, combined.

Returns a dict:
    {
        "sql": "... ? ... ?",       # parameterised SQL, safe to execute
        "params": [...],            # values for the '?' placeholders
        "display_sql": "... 'x' ...",  # same query with values inlined, for the UI
    }
"""

from typing import Tuple, List
from core.intent_parser import Intent
from core.schema_manager import SchemaManager

_schema = SchemaManager()

# Metric ids that have a hand-written, join-aware SQL branch below. Anything
# else reaching this module (e.g. a raw column name the Ambiguity Detector
# offered via its schema-introspection fallback for an entity with no
# registered Metric) is handled by _generic_ranking_fallback() instead of
# crashing -- this is what lets the generator cope with a metric it was
# never specifically pre-programmed for.
_KNOWN_METRIC_IDS = {
    "customer": {"revenue", "orders_count"},
    "product": {"revenue", "quantity", "price"},
    "order": {"value"},
}


def _quote(value) -> str:
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _inline(sql: str, params: list) -> str:
    display = sql
    for p in params:
        display = display.replace("?", _quote(p), 1)
    return display


def _build_filters(intent: Intent, alias_customer="c", alias_order="o", alias_product="p") -> Tuple[List[str], List]:
    clauses, params = [], []
    if intent.time_range:
        clauses.append(f"{alias_order}.order_date BETWEEN ? AND ?")
        params.extend(intent.time_range)
    if "status" in intent.filters:
        clauses.append(f"{alias_order}.status = ?")
        params.append(intent.filters["status"])
    if "city" in intent.filters:
        clauses.append(f"{alias_customer}.city = ?")
        params.append(intent.filters["city"])
    if "category" in intent.filters:
        clauses.append(f"{alias_product}.category = ?")
        params.append(intent.filters["category"])
    return clauses, params


def _where(clauses: List[str]) -> str:
    return f"WHERE {' AND '.join(clauses)}" if clauses else ""


def generate(intent: Intent) -> dict:
    entity = intent.target_entity

    # A metric was set, but it's not one of the ids this module has a
    # hand-written, join-aware branch for -- this happens when the Ambiguity
    # Detector resolved a ranking question via its raw-numeric-column
    # fallback (no Metric registered for this entity in the schema). Build
    # a safe, generic single-table ranking query directly from the schema
    # instead of failing, so an entity/metric combination nobody explicitly
    # coded for still produces a correct, safe query.
    if intent.metric and intent.metric not in _KNOWN_METRIC_IDS.get(entity, set()):
        sql, params = _generic_ranking_fallback(intent)
        return {"sql": sql, "params": params, "display_sql": _inline(sql, params)}

    if entity == "customer":
        sql, params = _generate_customer_query(intent)
    elif entity == "product":
        sql, params = _generate_product_query(intent)
    elif entity == "order":
        sql, params = _generate_order_query(intent)
    else:
        raise ValueError(f"Cannot generate SQL: unsupported target_entity '{entity}'")

    return {"sql": sql, "params": params, "display_sql": _inline(sql, params)}


def _generic_ranking_fallback(intent: Intent) -> Tuple[str, list]:
    """Rank a single table directly by a schema-verified numeric column that
    has no hand-written aggregation branch. No join required (raw column on
    the entity's own table), so this stays safe with a simple ORDER BY."""
    table = intent.target_entity if intent.target_entity.endswith("s") else intent.target_entity + "s"
    valid_columns = set(_schema.numeric_columns(intent.target_entity))
    if intent.metric not in valid_columns:
        raise ValueError(
            f"'{intent.metric}' is not a recognised numeric column on {table}; "
            f"valid options were: {', '.join(sorted(valid_columns)) or 'none'}."
        )
    direction = "ASC" if intent.sort_direction == "ASC" else "DESC"
    sql = f"SELECT * FROM {table} ORDER BY {intent.metric} {direction} LIMIT {int(intent.limit)}"
    return sql, []


# ---------------------------------------------------------------------------
def _generate_customer_query(intent: Intent) -> Tuple[str, list]:
    needs_orders = bool(intent.metric or intent.time_range or "status" in intent.filters or intent.group_by == "customer")

    # metric-driven ranking: "best customer" (revenue or orders_count), resolved
    if intent.metric == "revenue":
        clauses, params = _build_filters(intent)
        sql = (
            "SELECT c.customer_id, c.name, c.city, "
            "SUM(oi.quantity * oi.unit_price) AS revenue\n"
            "FROM customers c\n"
            "JOIN orders o ON o.customer_id = c.customer_id\n"
            "JOIN order_items oi ON oi.order_id = o.order_id\n"
            f"{_where(clauses)}\n"
            "GROUP BY c.customer_id, c.name, c.city\n"
            f"ORDER BY revenue {intent.sort_direction}\n"
            f"LIMIT {int(intent.limit)}"
        )
        return sql, params

    if intent.metric == "orders_count":
        clauses, params = _build_filters(intent)
        sql = (
            "SELECT c.customer_id, c.name, c.city, "
            "COUNT(DISTINCT o.order_id) AS orders_count\n"
            "FROM customers c\n"
            "JOIN orders o ON o.customer_id = c.customer_id\n"
            f"{_where(clauses)}\n"
            "GROUP BY c.customer_id, c.name, c.city\n"
            f"ORDER BY orders_count {intent.sort_direction}\n"
            f"LIMIT {int(intent.limit)}"
        )
        return sql, params

    if intent.group_by == "city":
        sql = (
            "SELECT city, COUNT(*) AS customer_count\n"
            "FROM customers\n"
            "GROUP BY city\n"
            "ORDER BY customer_count DESC"
        )
        return sql, []

    if intent.aggregation == "COUNT" and not needs_orders:
        clauses, params = [], []
        if "city" in intent.filters:
            clauses.append("city = ?")
            params.append(intent.filters["city"])
        sql = f"SELECT COUNT(*) AS customer_count FROM customers {_where(clauses)}".strip()
        return sql, params

    # plain listing
    clauses = []
    params = []
    if "city" in intent.filters:
        clauses.append("city = ?")
        params.append(intent.filters["city"])
    sql = (
        "SELECT customer_id, name, email, city, signup_date\n"
        "FROM customers\n"
        f"{_where(clauses)}\n"
        f"LIMIT {int(intent.limit)}"
    )
    return sql, params


# ---------------------------------------------------------------------------
def _generate_product_query(intent: Intent) -> Tuple[str, list]:
    if intent.metric == "revenue":
        clauses, params = _build_filters(intent, alias_product="p")
        sql = (
            "SELECT p.product_id, p.name, p.category, "
            "SUM(oi.quantity * oi.unit_price) AS revenue\n"
            "FROM products p\n"
            "JOIN order_items oi ON oi.product_id = p.product_id\n"
            "JOIN orders o ON o.order_id = oi.order_id\n"
            f"{_where(clauses)}\n"
            "GROUP BY p.product_id, p.name, p.category\n"
            f"ORDER BY revenue {intent.sort_direction}\n"
            f"LIMIT {int(intent.limit)}"
        )
        return sql, params

    if intent.metric == "quantity":
        clauses, params = _build_filters(intent, alias_product="p")
        sql = (
            "SELECT p.product_id, p.name, p.category, "
            "SUM(oi.quantity) AS units_sold\n"
            "FROM products p\n"
            "JOIN order_items oi ON oi.product_id = p.product_id\n"
            "JOIN orders o ON o.order_id = oi.order_id\n"
            f"{_where(clauses)}\n"
            "GROUP BY p.product_id, p.name, p.category\n"
            f"ORDER BY units_sold {intent.sort_direction}\n"
            f"LIMIT {int(intent.limit)}"
        )
        return sql, params

    if intent.metric == "price":
        # No join needed -- price lives directly on products. This branch is
        # what resolves "cheapest product" (ASC) / "priciest product" (DESC).
        clauses, params = [], []
        if "category" in intent.filters:
            clauses.append("category = ?")
            params.append(intent.filters["category"])
        sql = (
            "SELECT product_id, name, category, price\n"
            "FROM products\n"
            f"{_where(clauses)}\n"
            f"ORDER BY price {intent.sort_direction}\n"
            f"LIMIT {int(intent.limit)}"
        )
        return sql, params

    if intent.group_by == "category":
        sql = (
            "SELECT p.category, SUM(oi.quantity * oi.unit_price) AS revenue, "
            "SUM(oi.quantity) AS units_sold\n"
            "FROM products p\n"
            "JOIN order_items oi ON oi.product_id = p.product_id\n"
            "GROUP BY p.category\n"
            "ORDER BY revenue DESC"
        )
        return sql, []

    # plain listing
    clauses, params = [], []
    if "category" in intent.filters:
        clauses.append("category = ?")
        params.append(intent.filters["category"])
    sql = (
        "SELECT product_id, name, category, price\n"
        "FROM products\n"
        f"{_where(clauses)}\n"
        f"LIMIT {int(intent.limit)}"
    )
    return sql, params


# ---------------------------------------------------------------------------
def _needs_product_join(intent: Intent) -> bool:
    """An order-level query only needs order_items/products joined in if a
    category filter is actually present -- added as an explicit, named
    check (rather than always joining) so every order-query branch stays
    correct regardless of which filters happen to be combined with it."""
    return "category" in intent.filters


def _generate_order_query(intent: Intent) -> Tuple[str, list]:
    needs_product = _needs_product_join(intent)

    if intent.metric == "value":
        clauses, params = _build_filters(intent)
        joins = "JOIN order_items oi ON oi.order_id = o.order_id\n"
        if needs_product:
            joins += "JOIN products p ON p.product_id = oi.product_id\n"
        sql = (
            "SELECT o.order_id, c.name AS customer_name, "
            "SUM(oi.quantity * oi.unit_price) AS order_value\n"
            "FROM orders o\n"
            "JOIN customers c ON c.customer_id = o.customer_id\n"
            f"{joins}"
            f"{_where(clauses)}\n"
            "GROUP BY o.order_id, c.name\n"
            f"ORDER BY order_value {intent.sort_direction}\n"
            f"LIMIT {int(intent.limit)}"
        )
        return sql, params

    if intent.group_by == "customer":
        clauses, params = _build_filters(intent)
        joins = ""
        if needs_product:
            joins = ("JOIN order_items oi ON oi.order_id = o.order_id\n"
                      "JOIN products p ON p.product_id = oi.product_id\n")
        sql = (
            "SELECT c.customer_id, c.name, COUNT(DISTINCT o.order_id) AS orders_count\n"
            "FROM orders o\n"
            "JOIN customers c ON c.customer_id = o.customer_id\n"
            f"{joins}"
            f"{_where(clauses)}\n"
            "GROUP BY c.customer_id, c.name\n"
            "ORDER BY orders_count DESC"
        )
        return sql, params

    if intent.aggregation == "COUNT":
        clauses, params = _build_filters(intent)
        if needs_product:
            # order_items/products join fans out rows per line item, so a
            # plain COUNT(*) would overcount -- count distinct orders instead.
            sql = (
                "SELECT COUNT(DISTINCT o.order_id) AS order_count\n"
                "FROM orders o\n"
                "JOIN order_items oi ON oi.order_id = o.order_id\n"
                "JOIN products p ON p.product_id = oi.product_id\n"
                f"{_where(clauses)}"
            )
        else:
            sql = f"SELECT COUNT(*) AS order_count FROM orders o {_where(clauses)}".strip()
        return sql, params

    if intent.aggregation in ("SUM", "AVG"):
        func = intent.aggregation
        clauses, params = _build_filters(intent)
        joins = "JOIN order_items oi ON oi.order_id = o.order_id\n"
        if needs_product:
            joins += "JOIN products p ON p.product_id = oi.product_id\n"
        sql = (
            f"SELECT {func}(oi.quantity * oi.unit_price) AS revenue\n"
            "FROM orders o\n"
            f"{joins}"
            f"{_where(clauses)}"
        )
        return sql, params

    # plain listing: "show recent orders" / "show me orders" / filtered by status/time/city/category
    clauses, params = _build_filters(intent)
    joins = "JOIN customers c ON c.customer_id = o.customer_id\n"
    select_cols = "SELECT DISTINCT o.order_id, c.name AS customer_name, o.order_date, o.status\n"
    if needs_product:
        joins += ("JOIN order_items oi ON oi.order_id = o.order_id\n"
                   "JOIN products p ON p.product_id = oi.product_id\n")
    sql = (
        f"{select_cols}"
        "FROM orders o\n"
        f"{joins}"
        f"{_where(clauses)}\n"
        "ORDER BY o.order_date DESC\n"
        f"LIMIT {int(intent.limit)}"
    )
    return sql, params
