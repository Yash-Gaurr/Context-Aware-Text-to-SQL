"""
query_handler.py
------------------
MODULE: Query / Input Handler (pipeline orchestrator)

TWO-LAYER ARCHITECTURE
-----------------------
  SEMANTIC REASONING (probabilistic, swappable)   DETERMINISTIC SAFETY LAYER (fixed)
  ----------------------------------------------  -----------------------------------
  core/reasoner.py                                 core/sql_validator.py
    - GeminiReasoner    (LLM, free)                 core/db_executor.py
    - OpenRouterReasoner (LLM, free)                 core/result_formatter.py
    - OpenAIReasoner    (LLM, paid)
    - ClaudeReasoner    (LLM, paid)
    - RuleBasedReasoner (offline fallback)

This file is the seam between them, and it is written so that seam can
never be crossed in the unsafe direction: `reasoner.reason(...)` returns
either a clarification (shown straight to the user) or a SQL string, and
that SQL string is ALWAYS passed through sql_validator.validate() and only
ever executed via db_executor.execute()'s read-only connection. It does
not matter whether the SQL came from GPT, Claude, or the hand-written rule
engine -- every single one of them is untrusted input to this file below
the `reasoner.reason(...)` call, exactly like a raw HTTP request body
would be. That boundary, not any particular Reasoner, is the actual
"safe execution" guarantee requested for this project.

If the reasoner's response can't be trusted for ANY reason -- the validator
rejects the proposed SQL, the executor fails to run it, or the reasoner's
own output couldn't even be parsed as valid JSON (e.g. a truncated
response from a model that ran out of output tokens mid-reply) -- the
specific problem is fed back to the reasoner as if it were the next turn,
and it gets a bounded number of chances to self-correct (brief requirement
#4: "explain the issue and attempt correction rather than executing unsafe
SQL"), capped by MAX_SELF_CORRECTIONS so a persistently broken reasoner
can't loop forever or silently execute something unvalidated.
"""

from core import sql_validator, db_executor, result_formatter
from core.context_manager import ContextManager
from core.reasoner import Reasoner, ReasonerError, normalize_options

context_manager = ContextManager()

MAX_SELF_CORRECTIONS = 2


def handle_query(session_id: str, user_text: str, reasoner: Reasoner) -> dict:
    context_manager.log_turn(session_id, "user", user_text)
    conversation = context_manager.get_conversation(session_id)

    correction_note = None
    attempts = 0

    while True:
        turn_input = conversation
        if correction_note:
            # Feed the validator's/executor's/parser's rejection back as if
            # it were the next user turn, so the reasoner sees exactly what
            # went wrong and can propose a fix -- without this ever
            # bypassing the validator on the retry.
            turn_input = conversation + [{"role": "user", "content": correction_note}]

        try:
            decision = reasoner.reason(session_id, turn_input)
        except ReasonerError as exc:
            # A malformed, empty, or truncated response from the reasoner
            # (e.g. the model ran out of output tokens mid-JSON) is treated
            # exactly like a rejected SQL proposal below: retried with the
            # specific problem described, bounded by MAX_SELF_CORRECTIONS,
            # rather than failing the whole turn on the first hiccup.
            attempts += 1
            if attempts > MAX_SELF_CORRECTIONS:
                return {
                    "type": "error",
                    "stage": "reasoning",
                    "message": f"The reasoning backend failed to produce a usable response "
                               f"after {MAX_SELF_CORRECTIONS} attempt(s). Last issue: {exc}",
                    "original_query": user_text,
                }
            correction_note = (
                f"[system] Your previous response could not be used: {exc} "
                f"Respond again with ONLY the complete JSON object described in your "
                f"instructions -- no partial output, no text before or after it, no "
                f"markdown fences, and make sure it is fully closed/terminated."
            )
            continue

        if decision.get("type") == "clarification":
            if not decision.get("question"):
                # Same principle as the SQL-shape check below: a Reasoner
                # (including a custom/third-party one) is untrusted input.
                # Don't index into a key that might not exist.
                return {
                    "type": "error",
                    "stage": "reasoning",
                    "message": f"Reasoner returned a clarification with no question: {decision!r}",
                    "original_query": user_text,
                }
            # Normalize options HERE, not just inside reasoner.py's own LLM
            # JSON parsing -- this is the one point ALL reasoners' output
            # converges (built-in LLM backends, the rule-based engine, and
            # any custom Reasoner someone plugs in), so it's the reliable
            # place to guarantee the UI never renders an empty button
            # regardless of what shape a particular Reasoner handed back.
            decision["options"] = normalize_options(decision.get("options"))
            context_manager.log_turn(session_id, "assistant", decision["question"])
            response = {"type": "clarification", "original_query": user_text, **decision}
            context_manager.log_query(session_id, response)
            return response

        if decision.get("type") != "sql" or not str(decision.get("sql", "")).strip():
            # A Reasoner implementation returned something outside the
            # contract entirely (bypassing reasoner.py's own JSON-shape
            # validation, e.g. a custom/buggy Reasoner). Fail as a graceful
            # error -- never guess what was meant, never index into a key
            # that might not exist.
            return {
                "type": "error",
                "stage": "reasoning",
                "message": f"Reasoner returned an invalid response shape: {decision!r}",
                "original_query": user_text,
            }

        sql = decision["sql"]
        is_valid, reason = sql_validator.validate(sql)
        if not is_valid:
            attempts += 1
            if attempts > MAX_SELF_CORRECTIONS:
                return {
                    "type": "error",
                    "stage": "sql_validation",
                    "message": f"Could not produce a safe query after {MAX_SELF_CORRECTIONS} "
                               f"correction attempt(s). Last issue: {reason}",
                    "original_query": user_text,
                }
            correction_note = (
                f"[validator] The query you proposed was REJECTED and was never run: {reason} "
                f"Propose a corrected, safe, read-only SELECT query for the original question, "
                f"or ask a clarifying question if you're no longer sure what's safe."
            )
            continue

        try:
            columns, rows = db_executor.execute(sql, [])
        except db_executor.QueryExecutionError as exc:
            attempts += 1
            if attempts > MAX_SELF_CORRECTIONS:
                return {
                    "type": "error",
                    "stage": "execution",
                    "message": f"Query failed to execute after {MAX_SELF_CORRECTIONS} correction "
                               f"attempt(s). Last error: {exc}",
                    "original_query": user_text,
                }
            correction_note = (
                f"[executor] The query you proposed passed validation but FAILED to execute: {exc} "
                f"Propose a corrected query for the original question."
            )
            continue

        formatted = result_formatter.format_results(columns, rows)
        response = {
            "type": "result",
            "sql": sql,
            "explanation": decision.get("explanation", ""),
            "original_query": user_text,
            **formatted,
        }
        context_manager.log_turn(
            session_id, "assistant",
            f"[ran SQL] {sql}\n{formatted['summary']}"
        )
        context_manager.log_query(session_id, response)
        return response
