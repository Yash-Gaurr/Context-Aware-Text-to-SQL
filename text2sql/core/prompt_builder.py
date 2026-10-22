"""
prompt_builder.py
-------------------
MODULE: (feeds) Intent & Ambiguity Detector / Text-to-SQL Generator, LLM variant

Builds the system prompt that turns Claude/GPT into the project's semantic
reasoning layer. The prompt is assembled from LIVE data every time the
process starts -- table/column descriptions, the registered Metric
vocabulary (with exact SQL expressions, so "revenue" isn't left for the
model to reinvent), and the ACTUAL distinct values currently in the
database for every filterable column. None of this is copy-pasted prose;
if the database changes, the next run's prompt reflects it automatically.

This is the file to read if you want to see exactly what the LLM is and
isn't allowed to do -- the rules section is deliberately short and
unambiguous, because that's what keeps a general-purpose model behaving
like a disciplined component of a pipeline instead of a free-roaming
chatbot.
"""

from core.schema_manager import SchemaManager
from core.intent_parser import REFERENCE_TODAY  # kept in sync with the seeded dataset


def build_system_prompt() -> str:
    schema = SchemaManager()
    lines = []

    lines.append(
        "You are the semantic reasoning layer inside a text-to-SQL assistant for a small "
        "e-commerce SQLite database. You do NOT execute queries or touch the database "
        "yourself -- a separate, deterministic component validates and runs whatever SQL "
        "you propose. Your only two jobs are: (1) decide whether the conversation so far "
        "gives you enough information to write ONE single, unambiguous, correct, read-only "
        "SQL query, and (2) if it does not, ask a short and specific clarifying question "
        "instead of guessing."
    )
    lines.append("")

    lines.append("DATABASE SCHEMA")
    for table in schema.tables.values():
        lines.append(f"- {table.name} ({table.description}) -- primary key: {table.primary_key}")
        for c in table.columns:
            lines.append(f"    {c.name}: {c.dtype} -- {c.description}")
    lines.append("")

    lines.append("RELATIONSHIPS (how to join)")
    for r in schema.relationships:
        lines.append(f"- {r.from_table}.{r.from_column} = {r.to_table}.{r.to_column}")
    lines.append("")

    lines.append("REGISTERED BUSINESS METRICS -- use these exact SQL expressions and joins "
                  "whenever a question maps to one of them; do not reinvent the calculation:")
    for entity, metrics in schema.metrics.items():
        for m in metrics:
            lines.append(f"- {entity}.{m.id} (\"{m.label}\"): {m.sql_expression}")
            lines.append(f"    join: {m.join_hint}")
    lines.append("")

    lines.append("CURRENT DATA -- the exact values that exist RIGHT NOW (read live from the "
                  "database when this prompt was built; treat anything not in these lists as "
                  "not existing, and a near-miss like a typo as something to ask about, not guess):")
    for key, (table, column) in schema.filterable_columns.items():
        values = schema.distinct_values(table, column)
        lines.append(f"- {table}.{column}: {', '.join(values)}")
    lines.append("")

    lines.append(f"Treat {REFERENCE_TODAY.isoformat()} as TODAY when resolving relative time "
                  f"phrases such as \"last month\", \"this week\", or \"yesterday\".")
    lines.append("")

    lines.append("RULES (follow all of them, every turn)")
    lines.append(
        "1. You may only ever propose a single read-only SELECT statement. Never propose "
        "INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, multiple statements separated by "
        "';', or anything that writes to the database."
    )
    lines.append(
        "2. If the question could reasonably map to more than one different SQL query -- "
        "e.g. a ranking word like \"best\"/\"top\"/\"cheapest\" where more than one metric "
        "above could apply, a vague time word like \"recent\" with no fixed window, or a "
        "filter value that doesn't clearly match exactly one value in CURRENT DATA above -- "
        "do NOT guess. Ask a short, specific clarifying question with 2-4 concrete options "
        "drawn from what's actually available (the real metric names, or the real values "
        "from CURRENT DATA)."
    )
    lines.append(
        "3. If only one registered metric applies, or the question is otherwise fully "
        "determined by what's given, generate the SQL directly. Do not ask a clarifying "
        "question when there genuinely is only one reasonable interpretation."
    )
    lines.append(
        "4. When the latest user message is answering a clarifying question YOU asked "
        "earlier in this conversation, use the full conversation to resolve the original "
        "question with that answer, then produce the SQL -- do not ask the same thing again."
    )
    lines.append(
        "5. If a previous SQL proposal of yours was rejected (you will see a message saying "
        "so, with the reason), fix the specific problem named and propose a corrected query, "
        "or ask a clarifying question if you are no longer sure what is safe to run."
    )
    lines.append(
        "6. Respond with STRICT, COMPLETE JSON ONLY -- no markdown code fences, no prose "
        "before or after, no truncation -- matching EXACTLY one of these two shapes. Each "
        "clarification option MUST be an object with both an \"id\" (short machine value, "
        "e.g. \"revenue\") and a \"label\" (what the user sees, e.g. \"Highest total revenue\") "
        "-- do not return options as plain strings:\n"
        '   {"type": "clarification", "question": "...", '
        '"options": [{"id": "...", "label": "..."}, {"id": "...", "label": "..."}], "reason": "..."}\n'
        '   {"type": "sql", "sql": "SELECT ...", "explanation": "..."}\n'
        "Keep the \"sql\" and \"explanation\" values concise. Finish the JSON object completely "
        "in a single response -- an incomplete or cut-off JSON object is a failure."
    )

    return "\n".join(lines)
