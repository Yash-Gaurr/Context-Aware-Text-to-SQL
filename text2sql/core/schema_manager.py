"""
schema_manager.py
------------------
MODULE: Schema Manager

Responsible for knowing the shape of the database: tables, columns, types
and relationships. Every other module that needs to reason about the
database (the SQL generator, the validator, the UI's schema explorer) goes
through this module instead of hardcoding table/column names in multiple
places.

This is intentionally a static, hand-written description (rather than
introspected purely from sqlite_master) so that it can also carry semantic
information -- e.g. "this column is a date", "this is the metric used for
revenue" -- which plain SQL introspection cannot give us.
"""

import difflib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class Column:
    name: str
    dtype: str
    description: str = ""


@dataclass
class Table:
    name: str
    columns: List[Column]
    description: str = ""
    primary_key: str = ""


@dataclass
class Relationship:
    from_table: str
    from_column: str
    to_table: str
    to_column: str


@dataclass
class Metric:
    """A measurable quantity registered against an entity.

    This is the piece that lets the Ambiguity Detector *reason* about
    whether 'best <entity>' is well-defined, instead of a hardcoded rule
    per entity: the detector simply asks the schema 'how many ways can
    this entity be ranked?' and reacts to the count -- 0 (nothing to rank
    by -> fall back to raw numeric columns), 1 (no real ambiguity, only
    one sensible reading -> resolve it silently), or 2+ (genuinely
    ambiguous -> ask, using these exact labels).

    sql_expression / join_hint exist so the SAME registry can also ground
    the LLM reasoning layer (see prompt_builder.py): the model is told the
    exact SQL expression and joins each metric requires, rather than being
    left to infer the schema's business semantics ("revenue" is not just a
    column, it's SUM(quantity * unit_price) after two joins) from column
    names alone.
    """
    id: str
    label: str
    keywords: List[str]  # surface forms that name this metric directly and resolve it unambiguously
    sql_expression: str = ""   # e.g. "SUM(oi.quantity * oi.unit_price)"
    join_hint: str = ""        # e.g. "customers -> orders -> order_items"





class SchemaManager:
    """Central, queryable description of the e-commerce database schema."""

    def __init__(self):
        self.tables: Dict[str, Table] = {
            "customers": Table(
                name="customers",
                primary_key="customer_id",
                description="One row per customer.",
                columns=[
                    Column("customer_id", "INTEGER", "Unique customer id"),
                    Column("name", "TEXT", "Customer full name"),
                    Column("email", "TEXT", "Customer email"),
                    Column("city", "TEXT", "Customer's city"),
                    Column("signup_date", "TEXT (YYYY-MM-DD)", "Date the customer signed up"),
                ],
            ),
            "products": Table(
                name="products",
                primary_key="product_id",
                description="Product catalog.",
                columns=[
                    Column("product_id", "INTEGER", "Unique product id"),
                    Column("name", "TEXT", "Product name"),
                    Column("category", "TEXT", "Product category"),
                    Column("price", "REAL", "Catalog price"),
                ],
            ),
            "orders": Table(
                name="orders",
                primary_key="order_id",
                description="One row per order placed by a customer.",
                columns=[
                    Column("order_id", "INTEGER", "Unique order id"),
                    Column("customer_id", "INTEGER", "FK -> customers.customer_id"),
                    Column("order_date", "TEXT (YYYY-MM-DD)", "Date the order was placed"),
                    Column("status", "TEXT", "placed | shipped | delivered | cancelled"),
                ],
            ),
            "order_items": Table(
                name="order_items",
                primary_key="order_item_id",
                description="Line items belonging to an order (product + quantity + price paid).",
                columns=[
                    Column("order_item_id", "INTEGER", "Unique line item id"),
                    Column("order_id", "INTEGER", "FK -> orders.order_id"),
                    Column("product_id", "INTEGER", "FK -> products.product_id"),
                    Column("quantity", "INTEGER", "Units of the product purchased"),
                    Column("unit_price", "REAL", "Price per unit at time of purchase"),
                ],
            ),
        }

        self.relationships: List[Relationship] = [
            Relationship("orders", "customer_id", "customers", "customer_id"),
            Relationship("order_items", "order_id", "orders", "order_id"),
            Relationship("order_items", "product_id", "products", "product_id"),
        ]

        # ------------------------------------------------------------------
        # Metric registry: what CAN "best"/"top"/"cheapest" mean for each
        # entity? This is the data the Ambiguity Detector reasons over --
        # it is intentionally kept out of the parser/detector code so that
        # extending "what's measurable" never means touching detection logic.
        # ------------------------------------------------------------------
        self.metrics: Dict[str, List[Metric]] = {
            "customer": [
                Metric("revenue", "total revenue generated",
                       ["revenue", "spending", "spend", "money spent", "sales value", "amount spent"],
                       sql_expression="SUM(oi.quantity * oi.unit_price)",
                       join_hint="customers c JOIN orders o ON o.customer_id=c.customer_id "
                                 "JOIN order_items oi ON oi.order_id=o.order_id"),
                Metric("orders_count", "number of orders placed",
                       ["number of orders", "order count", "most orders", "orders placed", "frequency"],
                       sql_expression="COUNT(DISTINCT o.order_id)",
                       join_hint="customers c JOIN orders o ON o.customer_id=c.customer_id"),
            ],
            "product": [
                Metric("revenue", "total revenue earned",
                       ["revenue", "sales value", "earnings", "highest sales", "most money"],
                       sql_expression="SUM(oi.quantity * oi.unit_price)",
                       join_hint="products p JOIN order_items oi ON oi.product_id=p.product_id "
                                 "JOIN orders o ON o.order_id=oi.order_id"),
                Metric("quantity", "units sold",
                       ["quantity", "units sold", "sold", "most sold", "number sold", "popularity"],
                       sql_expression="SUM(oi.quantity)",
                       join_hint="products p JOIN order_items oi ON oi.product_id=p.product_id "
                                 "JOIN orders o ON o.order_id=oi.order_id"),
                Metric("price", "listed price",
                       ["price", "cheap", "expensive", "cheapest", "priciest", "cost"],
                       sql_expression="price",
                       join_hint="products (no join needed, price is a direct column)"),
            ],
            "order": [
                Metric("value", "order value (line items total)",
                       ["value", "amount", "order total"],
                       sql_expression="SUM(oi.quantity * oi.unit_price)",
                       join_hint="orders o JOIN order_items oi ON oi.order_id=o.order_id"),
            ],
        }

        # Which (table, column) a recognised filter keyword maps to. Values
        # are matched against what's ACTUALLY in the database at query time
        # (see distinct_values / resolve_filter_value below) rather than a
        # hardcoded list of valid cities/categories/statuses in Python code.
        self.filterable_columns: Dict[str, Tuple[str, str]] = {
            "city": ("customers", "city"),
            "category": ("products", "category"),
            "status": ("orders", "status"),
        }

    # ------------------------------------------------------------------
    def table_names(self) -> List[str]:
        return list(self.tables.keys())

    def metrics_for(self, entity: str) -> List[Metric]:
        """All metrics registered for an entity ('customer', 'product', 'order')."""
        return self.metrics.get(entity, [])

    def numeric_columns(self, entity: str) -> List[str]:
        """Fallback used when NO metric is registered for an entity: introspect
        the schema itself for plausible ranking columns (numeric, not an id/FK).
        This is what lets the system reason about a ranking question on an
        entity nobody explicitly wired a Metric for."""
        table_name = entity if entity.endswith("s") else entity + "s"
        table = self.tables.get(table_name)
        if not table:
            return []
        result = []
        for c in table.columns:
            if c.name == table.primary_key or c.name.endswith("_id"):
                continue
            if c.dtype.split()[0] in ("INTEGER", "REAL"):
                result.append(c.name)
        return result

    def distinct_values(self, table: str, column: str) -> List[str]:
        """Reads the ACTUAL distinct values currently in the database for a
        given column. This is what lets filter-value ambiguity be resolved
        by checking against real data instead of a hardcoded word list --
        table/column always come from filterable_columns (trusted, static),
        never from raw user text, so this is safe to run directly."""
        from core import db_executor
        sql = f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL ORDER BY {column}"
        try:
            _, rows = db_executor.execute(sql, [])
            return [str(r[column]) for r in rows]
        except Exception:
            return []

    def resolve_filter_value(self, key: str, raw_value: str) -> Tuple[Optional[str], List[str]]:
        """Reason about a raw filter value the parser extracted (e.g. city
        candidate text 'puna') against what's really in the database.

        Returns (resolved_value, alternatives):
          - resolved_value is set when there's exactly one sensible match
            (exact, single substring, or single close/fuzzy match) -- the
            ambiguity is over, nothing to ask.
          - resolved_value is None when it's genuinely unclear (zero or
            multiple plausible matches); 'alternatives' then holds the real
            candidate values from the database to offer the user.
        """
        table, column = self.filterable_columns.get(key, (None, None))
        if not table:
            return raw_value, []  # unknown filter key -> nothing to ground against
        known_values = self.distinct_values(table, column)
        if not known_values:
            return raw_value, []

        exact = [v for v in known_values if v.lower() == raw_value.lower()]
        if len(exact) == 1:
            return exact[0], []

        substring = [v for v in known_values if raw_value.lower() in v.lower()]
        if len(substring) == 1:
            return substring[0], []
        if len(substring) > 1:
            return None, substring

        close = difflib.get_close_matches(raw_value, known_values, n=5, cutoff=0.55)
        if len(close) == 1:
            return close[0], []
        return None, close

    def as_prompt_string(self) -> str:
        """A compact textual description of the schema, useful for logging
        and for the 'why the system generated this SQL' explanation shown
        in the UI. Kept separate from as_dict() which is for the UI's
        schema explorer widget."""
        lines = []
        for table in self.tables.values():
            cols = ", ".join(f"{c.name} ({c.dtype})" for c in table.columns)
            lines.append(f"{table.name}({cols})")
        for r in self.relationships:
            lines.append(f"{r.from_table}.{r.from_column} -> {r.to_table}.{r.to_column}")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "tables": {
                t.name: {
                    "description": t.description,
                    "primary_key": t.primary_key,
                    "columns": [
                        {"name": c.name, "type": c.dtype, "description": c.description}
                        for c in t.columns
                    ],
                }
                for t in self.tables.values()
            },
            "relationships": [
                {"from": f"{r.from_table}.{r.from_column}", "to": f"{r.to_table}.{r.to_column}"}
                for r in self.relationships
            ],
        }
