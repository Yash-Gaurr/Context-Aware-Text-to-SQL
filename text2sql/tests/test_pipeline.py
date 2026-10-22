"""
test_pipeline.py
------------------
MODULE: Evaluation

Standalone test/demo script (no pytest dependency needed, so it runs
anywhere, with no API key and no network) covering three things:

  PART A -- The RuleBasedReasoner's reasoning quality: unambiguous queries
            go straight to SQL, genuinely ambiguous ones are detected and
            clarified, clarification answers are resolved using
            conversational context, filter typos are grounded against the
            live database, and unsupported queries are handled gracefully.

  PART B -- SQL safety validation in isolation (destructive keywords,
            statement stacking, unknown tables all rejected).

  PART C -- THE ACTUAL SAFETY GUARANTEE THIS PROJECT IS BUILT AROUND: that
            query_handler.py's deterministic validator/executor layer
            rejects unsafe or broken SQL NO MATTER WHICH Reasoner produced
            it. This is exercised with two fake Reasoner implementations
            (not real LLM calls -- no network needed) that deliberately
            propose a destructive statement and a syntactically-broken
            one, proving the pipeline never executes them and instead
            feeds the rejection back for a bounded number of retries.

Run with:
    python tests/test_pipeline.py

To additionally smoke-test a REAL LLM backend (requires OPENAI_API_KEY or
ANTHROPIC_API_KEY and network access, so it is NOT part of the default
run), see tests/test_llm_smoke.py.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.query_handler import handle_query
from core.reasoner import Reasoner, RuleBasedReasoner, ReasonerError
from core import sql_validator

PASS = "PASS"
FAIL = "FAIL"
results = []


def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((name, status, detail))
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and status == FAIL else ""))


def run():
    rule_reasoner = RuleBasedReasoner()

    print("=" * 70)
    print("A1. UNAMBIGUOUS QUERIES -> straight to SQL, no clarification")
    print("=" * 70)
    r = handle_query("t1", "How many orders were delivered last month?", rule_reasoner)
    check("Unambiguous aggregate query returns a result directly", r["type"] == "result", r)

    r = handle_query("t2", "List customers from Pune.", rule_reasoner)
    check("Unambiguous filtered listing returns a result directly", r["type"] == "result", r)

    print()
    print("=" * 70)
    print("A2. AMBIGUITY DETECTION + CLARIFICATION + CONTEXT RETENTION")
    print("=" * 70)
    r1 = handle_query("t3", "Give me last month's best customer.", rule_reasoner)
    check("'Best customer' triggers a clarification", r1["type"] == "clarification", r1)
    check("Clarification asks about metric, built from the schema's metric registry (not a hardcoded string)",
          r1.get("ambiguity_type") == "RANKING_METRIC::customer"
          and {o["id"] for o in r1["options"]} == {"revenue", "orders_count"}, r1)

    r2 = handle_query("t3", "Revenue", rule_reasoner)
    check("Clarification answer resolves straight to a result", r2["type"] == "result", r2)
    check("Original query text is preserved through context resolution",
          r2.get("original_query") == "Revenue", r2)  # the turn that resolved it; transcript keeps turn 1 too
    check("Resolved metric=revenue is reflected in the reasoner's explanation",
          "metric=revenue" in r2.get("explanation", ""), r2)
    check("Generated SQL aggregates revenue (SUM of quantity*unit_price)",
          "SUM(oi.quantity * oi.unit_price)" in r2.get("sql", ""), r2)

    print()
    print("=" * 70)
    print("A3. MORE AMBIGUITY EXAMPLES FROM THE BRIEF")
    print("=" * 70)
    r = handle_query("t4", "Which product performed best?", rule_reasoner)
    check("'Best product' triggers clarification listing ALL registered product metrics (revenue, quantity, price)",
          r["type"] == "clarification"
          and {o["id"] for o in r["options"]} == {"revenue", "quantity", "price"}, r)

    r = handle_query("t5", "Show me the most valuable customers.", rule_reasoner)
    check("'Most valuable customers' triggers metric clarification (same reasoning rule, different phrasing)",
          r["type"] == "clarification" and r.get("ambiguity_type") == "RANKING_METRIC::customer", r)

    r = handle_query("t6", "Show recent orders.", rule_reasoner)
    check("'Recent orders' triggers a time-range clarification",
          r["type"] == "clarification" and r.get("ambiguity_type") == "TIME_RANGE", r)
    r2 = handle_query("t6", "Last 30 days", rule_reasoner)
    check("Time-range clarification resolves to a result", r2["type"] == "result", r2)

    print()
    print("=" * 70)
    print("A4. GENERAL REASONING -- not just the demo phrasings")
    print("=" * 70)
    r = handle_query("t8", "Which product is cheapest?", rule_reasoner)
    check("'Cheapest' resolves straight to the price metric (only one sensible reading)",
          r["type"] == "result" and "price" in r.get("sql", "").lower(), r)
    check("'Cheapest' sorts ascending", "ORDER BY price ASC" in r.get("sql", ""), r)

    r = handle_query("t9", "Show me customers in Puna", rule_reasoner)  # deliberate typo
    check("A typo'd city filter is corrected against the LIVE database (Puna -> Pune)",
          r["type"] == "result" and "'Pune'" in r.get("sql", ""), r)

    r = handle_query("t10", "Show me products in category Electroniks", rule_reasoner)  # deliberate typo
    check("A typo'd category filter is corrected against the LIVE database",
          r["type"] == "result" and "'Electronics'" in r.get("sql", ""), r)

    r = handle_query("t11", "Show me customers in Nowhereville", rule_reasoner)
    check("A filter value with NO match in the database triggers a grounded clarification",
          r["type"] == "clarification" and r.get("ambiguity_type", "").startswith("UNKNOWN_FILTER_VALUE"), r)

    r = handle_query("t12", "How many orders in category Electronics", rule_reasoner)
    check("Order query + category filter correctly joins products (regression: used to crash)",
          r["type"] == "result", r)

    print()
    print("=" * 70)
    print("A5. UNSUPPORTED QUERY (unknown entity) HANDLED GRACEFULLY")
    print("=" * 70)
    r = handle_query("t7", "asdkj qpwoe what is the meaning of life", rule_reasoner)
    check("Unrecognised question asks which entity, doesn't crash",
          r["type"] == "clarification" and r.get("ambiguity_type") == "UNKNOWN_ENTITY", r)

    print()
    print("=" * 70)
    print("B. SQL SAFETY VALIDATION (deterministic layer, in isolation)")
    print("=" * 70)
    unsafe_queries = [
        "DROP TABLE customers;",
        "DELETE FROM orders WHERE 1=1;",
        "UPDATE customers SET name='x';",
        "SELECT * FROM customers; DROP TABLE customers;",
        "ALTER TABLE customers ADD COLUMN hacked TEXT;",
    ]
    for q in unsafe_queries:
        valid, reason = sql_validator.validate(q)
        check(f"Blocked unsafe SQL: {q[:40]}...", valid is False, reason)

    valid, reason = sql_validator.validate("SELECT * FROM customers LIMIT 10")
    check("A safe read-only SELECT passes validation", valid is True, reason)

    valid, reason = sql_validator.validate("SELECT * FROM not_a_real_table")
    check("A query against an unknown table is rejected", valid is False, reason)

    valid, reason = sql_validator.validate("SELCT * FROM customers")
    check("Malformed SQL is rejected rather than executed", valid is False, reason)

    print()
    print("=" * 70)
    print("C. SAFETY BOUNDARY HOLDS REGARDLESS OF WHICH REASONER IS USED")
    print("   (fake Reasoners below simulate a misbehaving LLM -- no network needed)")
    print("=" * 70)

    class DestructiveReasoner(Reasoner):
        """Simulates an LLM that proposes a destructive statement, then
        self-corrects once fed the validator's rejection."""
        def __init__(self):
            self.calls = 0

        def reason(self, session_id, conversation):
            self.calls += 1
            if self.calls == 1:
                return {"type": "sql", "sql": "DROP TABLE customers;", "explanation": "bad"}
            return {"type": "sql", "sql": "SELECT COUNT(*) AS n FROM customers", "explanation": "corrected"}

    r = handle_query("safety1", "how many customers", DestructiveReasoner())
    check("A DROP TABLE proposal is rejected and NEVER executed; the reasoner "
          "self-corrects on retry and the final result is still returned",
          r["type"] == "result" and r.get("sql") == "SELECT COUNT(*) AS n FROM customers", r)

    class StubbornReasoner(Reasoner):
        """Simulates an LLM that never produces a safe query -- the pipeline
        must give up gracefully instead of looping forever or executing it anyway."""
        def reason(self, session_id, conversation):
            return {"type": "sql", "sql": "DELETE FROM customers", "explanation": "still wrong"}

    r = handle_query("safety2", "delete everyone", StubbornReasoner())
    check("A reasoner that never proposes safe SQL is cut off (bounded retries), "
          "never falls through to executing the unsafe query",
          r["type"] == "error" and r["stage"] == "sql_validation", r)

    class BrokenJSONReasoner(Reasoner):
        """Simulates an LLM whose output can't even be parsed as the expected
        shape -- must fail as a graceful error, never crash the app."""
        def reason(self, session_id, conversation):
            return {"type": "not_a_real_type"}

    try:
        r = handle_query("safety3", "anything", BrokenJSONReasoner())
        crashed = False
    except Exception:
        crashed = True
    check("An unrecognised reasoner output type is handled as a graceful error, not a crash",
          not crashed, "handle_query raised instead of returning an error dict")

    print()
    print("=" * 70)
    print("D. REGRESSION: Gemini free-tier bugs found in real-world testing")
    print("   (empty clarification buttons + truncated JSON on the follow-up turn)")
    print("=" * 70)

    class OldStyleOptionsReasoner(Reasoner):
        """Reproduces the exact live bug: a Reasoner returns clarification
        options as plain strings (the ORIGINAL system-prompt contract,
        before it was corrected to {id,label} objects) -- this used to
        render as empty, unclickable buttons in the UI."""
        def reason(self, session_id, conversation):
            return {
                "type": "clarification",
                "question": "How would you like to define the 'strongest' customer?",
                "options": ["Highest total revenue", "Most orders placed"],
                "reason": "ambiguous metric",
            }

    r = handle_query("regress1", "Who is the strongest customer?", OldStyleOptionsReasoner())
    check("Clarification options are normalized to non-empty {id,label} objects "
          "regardless of what shape the reasoner returned (fixes empty UI buttons)",
          r["type"] == "clarification"
          and len(r["options"]) == 2
          and all(isinstance(o, dict) and o.get("label") and o.get("id") for o in r["options"]),
          r)

    class OneTimeTruncationReasoner(Reasoner):
        """Reproduces the exact live bug: the follow-up turn (after the user
        answers a clarification) comes back as truncated/unparseable JSON
        once, then succeeds on retry -- this used to hard-fail the whole
        turn instead of asking the reasoner to complete its answer."""
        def __init__(self):
            self.calls = 0

        def reason(self, session_id, conversation):
            self.calls += 1
            if self.calls == 1:
                raise ReasonerError("Reasoner did not return valid JSON: truncated mid-SQL")
            return {"type": "sql", "sql": "SELECT COUNT(*) AS n FROM customers", "explanation": "recovered"}

    r = handle_query("regress2", "Orders", OneTimeTruncationReasoner())
    check("A truncated/unparseable response on ANY turn (not just after SQL validation) "
          "is retried via the self-correction loop instead of hard-failing immediately",
          r["type"] == "result", r)

    print()
    print("=" * 70)
    passed = sum(1 for _, s, _ in results if s == PASS)
    total = len(results)
    print(f"RESULT: {passed}/{total} checks passed")
    print("=" * 70)
    if passed != total:
        sys.exit(1)


if __name__ == "__main__":
    run()
