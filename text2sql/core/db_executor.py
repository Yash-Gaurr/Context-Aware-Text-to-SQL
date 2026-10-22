"""
db_executor.py
----------------
MODULE: Database Executor

Executes an already-validated, parameterised SQL statement against the
SQLite database and returns rows as plain dicts. Two independent safety
nets are used here, on top of the SQL Validator that already ran:

  1. The connection itself is opened in SQLite's read-only URI mode
     (mode=ro), so even a bug elsewhere in the pipeline physically cannot
     write to the database from this connection.
  2. A hard row cap (MAX_ROWS) protects the UI/demo from an unexpectedly
     huge result set.
"""

import sqlite3
import os
from typing import List, Tuple

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "database", "ecommerce.db")
MAX_ROWS = 200


class QueryExecutionError(Exception):
    pass


def execute(sql: str, params: list) -> Tuple[List[str], List[dict]]:
    uri = f"file:{DB_PATH}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchmany(MAX_ROWS)
        columns = [desc[0] for desc in cur.description] if cur.description else []
        result = [dict(row) for row in rows]
        conn.close()
        return columns, result
    except sqlite3.Error as exc:
        raise QueryExecutionError(str(exc)) from exc
