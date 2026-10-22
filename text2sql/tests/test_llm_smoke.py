"""
test_llm_smoke.py
--------------------
MODULE: Evaluation (optional, live)

test_pipeline.py deliberately never calls a real LLM (no API key, no
network needed, so it always runs -- in CI, in the sandbox, during a viva
with no wifi). This file is the complement: a small, honest smoke test of
whichever LLM backend you actually have configured, run OVER THE NETWORK
against the real OpenAI or Anthropic API.

It is skipped automatically (with a clear message, not a failure) if
neither OPENAI_API_KEY nor ANTHROPIC_API_KEY is set, or if the 'openai'/
'anthropic' packages aren't installed -- so it never blocks the main
evaluation run.

Run with:
    export OPENAI_API_KEY=sk-...      # or ANTHROPIC_API_KEY=...
    python tests/test_llm_smoke.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    for env_path in [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env"),
    ]:
        if os.path.exists(env_path):
            load_dotenv(env_path)
except ImportError:
    pass

from core.query_handler import handle_query
from core.reasoner import get_reasoner, ReasonerError


def run():
    try:
        reasoner, warning = get_reasoner(preference=os.environ.get("REASONER", "auto"))
    except ReasonerError as exc:
        print(f"SKIPPED: {exc}")
        return

    if reasoner.label().startswith("Rule-based"):
        print("SKIPPED: no GEMINI_API_KEY, OPENROUTER_API_KEY, OPENAI_API_KEY, or "
              "ANTHROPIC_API_KEY configured -- nothing to smoke-test against a real LLM. "
              "This is expected in an offline environment; it is not a failure.")
        return

    print(f"Smoke-testing live reasoning backend: {reasoner.label()}")
    print("=" * 70)

    session = "llm-smoke-1"
    r1 = handle_query(session, "Give me last month's best customer.", reasoner)
    print("Turn 1 ->", r1["type"], "|", r1.get("question") or r1.get("summary"))
    assert r1["type"] == "clarification", (
        f"Expected the LLM to ask for clarification on an ambiguous 'best customer' "
        f"question, got: {r1}"
    )

    r2 = handle_query(session, "revenue", reasoner)
    print("Turn 2 ->", r2["type"], "|", r2.get("summary"))
    assert r2["type"] == "result", f"Expected the clarification to resolve to a result, got: {r2}"
    assert "customer" in r2.get("sql", "").lower(), r2

    r3 = handle_query("llm-smoke-2", "List customers from Pune.", reasoner)
    print("Turn 3 ->", r3["type"], "|", r3.get("summary"))
    assert r3["type"] == "result", f"Expected an unambiguous query to skip clarification, got: {r3}"

    print("=" * 70)
    print("LLM smoke test passed.")


if __name__ == "__main__":
    run()
