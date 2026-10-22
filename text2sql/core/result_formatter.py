"""
result_formatter.py
---------------------
MODULE: Result Formatter

Turns the raw (columns, rows) pair from the Database Executor into what the
UI actually needs: a table-ready structure plus a short one-line, plain
English summary such as "1 row" or "Found 12 orders". Currency-like columns
(revenue, price, unit_price) get formatted with 2 decimal places.
"""

from typing import List

CURRENCY_COLUMNS = {"revenue", "price", "unit_price"}


def format_results(columns: List[str], rows: List[dict]) -> dict:
    formatted_rows = []
    for row in rows:
        formatted = {}
        for col in columns:
            value = row.get(col)
            if col in CURRENCY_COLUMNS and isinstance(value, (int, float)):
                formatted[col] = round(value, 2)
            else:
                formatted[col] = value
        formatted_rows.append(formatted)

    if not formatted_rows:
        summary = "No matching rows were found."
    elif len(formatted_rows) == 1 and len(columns) <= 2:
        # e.g. a single aggregate value like COUNT(*) or SUM(...)
        val = list(formatted_rows[0].values())[0]
        summary = f"Result: {val}"
    else:
        summary = f"{len(formatted_rows)} row{'s' if len(formatted_rows) != 1 else ''} returned."

    return {"columns": columns, "rows": formatted_rows, "summary": summary}
