# Context-Aware Text-to-SQL with an Intelligent Clarification Engine

A B.Tech mini project. You ask a database a question in plain English. If
the question is genuinely ambiguous — like *"best customer"*, which could
mean "most orders" or "highest revenue" — the system does not guess. It
asks a specific follow-up question, remembers what you originally asked,
folds your answer back into that original request, and only then generates
and runs the SQL.

**The architecture is two layers, deliberately separated:**

1. **Semantic reasoning (probabilistic, LLM-backed).** Understanding the
   question, recognising ambiguity, phrasing the clarifying question, and
   writing the candidate SQL — this is delegated to an LLM (OpenAI by
   default; Claude also supported), because that is genuinely what LLMs are
   good at, and hand-writing every phrasing a user might type does not scale.
2. **Deterministic safety (rule-based, fixed).** Whatever SQL the LLM
   proposes is **never trusted**. It is always run through a hand-written,
   from-scratch SQL Validator (read-only, single-statement, known-tables
   only) and executed only via a **read-only** SQLite connection. This layer
   contains no AI at all — it's ordinary, auditable Python — and it is what
   actually protects the database, regardless of what the LLM says.

If you take away one sentence from this README: **the LLM decides what
question to ask; it never decides what runs against the database.**

## Quick start (free — recommended if you don't have a paid API budget)

```bash
pip install -r requirements.txt
python database/setup_db.py                 # (re)builds and seeds the SQLite database
export GEMINI_API_KEY=...                    # free, no credit card: https://aistudio.google.com/apikey
python app.py                                # starts the web app on http://localhost:5000
```

Gemini's free tier (as of writing) allows roughly 15 requests/minute and
1500/day on its Flash models — comfortably enough for developing and
demoing this project. Rate limits are set by Google and can change, so
double-check at ai.google.dev/gemini-api/docs/rate-limits if you hit one.

**Alternative free option — OpenRouter** (routes to whichever open-weight
model is currently free): get a key at openrouter.ai/keys (no card needed),
then:
```bash
export OPENROUTER_API_KEY=sk-or-...
export REASONER=openrouter
```
OpenRouter's free model lineup (IDs ending in `:free`) rotates over time —
check openrouter.ai/models?max_price=0 for what's currently free, and if
the default in `core/reasoner.py` (`meta-llama/llama-3.3-70b-instruct:free`)
has been retired, set `TEXT2SQL_MODEL` to whatever's current:
```bash
export TEXT2SQL_MODEL="some-current-model:free"
```

No API key at all handy? The app still runs — it automatically falls back
to a deterministic, rule-based reasoning engine (see "Reasoning backends"
below) and tells you it did so, both in the terminal and in the UI's
top-right badge.

Open `http://localhost:5000` in a browser. Try the example from the brief:

```
You:    Give me last month's best customer.
System: Do you mean the customer with the most orders, or the one with the
        highest revenue?
You:    Revenue.
System: [runs SQL, shows the customer with highest revenue in the resolved
         "last month" date range]
```

Run the automated evaluation suite (30 checks, no API key or network
needed — see "Testing" below for why):

```bash
python tests/test_pipeline.py
```

## Reasoning backends, one interface

Everything upstream of SQL execution goes through a single `Reasoner`
interface (`core/reasoner.py`):

```python
class Reasoner(ABC):
    def reason(self, session_id, conversation) -> dict:
        """Returns either:
             {"type": "clarification", "question": ..., "options": [...], "reason": ...}
           or
             {"type": "sql", "sql": "SELECT ...", "explanation": ...}
        """
```

Five implementations exist, chosen at startup via the `REASONER`
environment variable (`auto` is the default):

| `REASONER=` | Class | Requires | Cost | What it does |
|---|---|---|---|---|
| `gemini` | `GeminiReasoner` | `GEMINI_API_KEY` | **Free** | Google's Gemini API, via the official `google-genai` SDK |
| `openrouter` | `OpenRouterReasoner` | `OPENROUTER_API_KEY` | **Free** (rotating model list) | Routes to a currently-free open-weight model through an OpenAI-compatible endpoint |
| `openai` | `OpenAIReasoner` | `OPENAI_API_KEY` | Paid | OpenAI's Chat Completions API (JSON mode) |
| `claude` | `ClaudeReasoner` | `ANTHROPIC_API_KEY` | Paid | Anthropic's Messages API |
| `rule` | `RuleBasedReasoner` | nothing | Free, offline | The hand-written, schema-grounded reasoning engine (kept from an earlier iteration of this project) — fully deterministic, no network needed |

`auto` tries the free backends first (Gemini, then OpenRouter), then the
paid ones, then falls back to the rule-based engine so the app always
starts regardless of what's configured. Which one is active is shown in
the web UI (top-right badge) and via `GET /api/mode`.

**Why keep the rule-based one at all, if the point is to use an LLM?**
Two reasons: (1) it means the project still runs and can be demoed with
zero setup and zero API cost, which matters for a viva where wifi/API
access can't be guaranteed; and (2) it's a genuine, working comparison —
you can run the exact same questions through both and discuss the
trade-offs (the rule engine is faster, free, and 100% predictable, but only
covers vocabulary someone thought to register; the LLM generalises to
phrasings nobody wrote a rule for, at the cost of needing an API key and
being probabilistic).

## The actual safety boundary

This is the part worth being precise about in viva, because "we used an
LLM but kept it safe" is a specific, checkable engineering claim, not a
slogan.

```
   User's natural language question
              │
              ▼
   ┌────────────────────────────┐
   │   REASONER (swappable)      │   core/reasoner.py
   │  Gemini/OpenRouter/OpenAI/    │  <- probabilistic, or at least
   │  Claude/rule-based            │     "black box" from the pipeline's POV
   └───────────┬─────────────────┘
               │  proposes SQL (UNTRUSTED, just a string)
               ▼
   ┌────────────────────────────┐
   │   SQL VALIDATOR             │   core/sql_validator.py
   │   - single statement only    │  <- deterministic, hand-written,
   │   - must start with SELECT   │     no AI, fully auditable
   │   - no DROP/DELETE/UPDATE/   │
   │     ALTER/TRUNCATE/etc.      │
   │   - only known tables        │
   └───────────┬─────────────────┘
               │  rejected? -> fed back to the Reasoner as a correction
               │  request, retried up to MAX_SELF_CORRECTIONS (2) times,
               │  then a graceful error -- NEVER executed regardless
               ▼  accepted
   ┌────────────────────────────┐
   │   DATABASE EXECUTOR          │   core/db_executor.py
   │   - SQLite opened mode=ro    │  <- a second, independent safety
   │     (physically read-only)   │     net: even a validator bug can't
   │   - hard row cap (200)       │     let a write reach the database
   └───────────┬─────────────────┘
               ▼
        Result Formatter -> UI
```

`core/query_handler.py` is where this is wired, and it is short and
readable specifically so you can point to the exact lines in viva: every
single path from `reasoner.reason(...)` to `db_executor.execute(...)`
passes through `sql_validator.validate(...)` first, with no exceptions —
not for the LLM backends, not for the rule-based one, not even for a fake
"malicious" test `Reasoner`.

`tests/test_pipeline.py` proves this directly (see the section marked
`SAFETY BOUNDARY HOLDS REGARDLESS OF WHICH REASONER IS USED`): it defines
a fake `Reasoner` that deliberately proposes `DROP TABLE customers;` and
confirms the pipeline rejects it, feeds the rejection back, and only
executes the corrected query on retry — a second fake `Reasoner` that
*never* self-corrects is confirmed to be cut off after 2 attempts rather
than looping forever or executing the unsafe query anyway. No real LLM
call is needed to prove this — the boundary is enforced in ordinary Python
that runs regardless of what called it.

## How the reasoning is grounded (not a free-roaming chatbot)

The LLM backends are given a system prompt (`core/prompt_builder.py`) built
**live** from the database every time the process starts:

- Every table/column and its description (`SchemaManager.tables`).
- A **metric registry** — the exact SQL expression and joins for each
  measurable quantity ("customer revenue" = `SUM(oi.quantity * oi.unit_price)`
  via a specific join path), so the model doesn't have to reinvent business
  semantics from column names alone, and can't quietly compute "revenue"
  a different way each time.
- The **actual, current distinct values** of every filterable column
  (real city names, real category names, real order statuses), read from
  the live SQLite database at prompt-build time — so a typo like "Puna" can
  be recognised as not matching anything real, rather than the model
  guessing or hallucinating a value.
- An explicit, short rule set: only ever propose one read-only `SELECT`;
  ask instead of guessing whenever more than one registered metric could
  apply, or a filter value doesn't clearly match the real data above; when
  a previous proposal was rejected, fix the *specific* problem named.

The full conversation transcript is resent on every call (LLM APIs are
stateless — see `ContextManager.get_conversation()`), which is what lets
the model resolve "Revenue" as an answer to *its own* earlier clarifying
question: context retention here is simply "the model can read the
transcript it already produced," not a hand-built slot-filling structure.

## Database (sample dataset)

SQLite, 4 tables: `customers`, `products`, `orders`, `order_items` (an
order has 1..N line items, each with a product, quantity and unit price —
this is what lets "revenue" and "orders count" be genuinely different
metrics). Seed data is generated deterministically (`random.seed(42)`) and
anchored to a fixed reference date (2026-09-07) instead of the real system
clock, specifically so that time-based demos ("last month", "recent") are
reproducible every time you run `setup_db.py` and during the viva.

## Testing

Two test scripts, for a specific reason:

- **`tests/test_pipeline.py`** — the main evaluation suite, 30 checks, runs
  with **no API key and no network**. It exercises the rule-based reasoner's
  behaviour directly (ambiguity detection, clarification, context
  retention, filter grounding) and — critically — proves the safety
  boundary above using fake `Reasoner` stand-ins. This is what always runs,
  in CI, in a viva room with no wifi, anywhere.
- **`tests/test_llm_smoke.py`** — an optional, honest smoke test of
  whichever *real* LLM backend you have configured. It skips itself
  cleanly (not a failure) if no API key is set. Run it once with your key
  exported to confirm the live backend behaves the same way, before your
  viva, when you do have network access.

```bash
python tests/test_pipeline.py          # always runs, proves the safety boundary
export GEMINI_API_KEY=...
python tests/test_llm_smoke.py         # optional, proves the real LLM works
```

## Project structure

```
text2sql/
├── app.py                      # Flask web layer; selects the Reasoner at startup
├── requirements.txt
├── database/
│   ├── setup_db.py             # schema + deterministic seed data
│   └── ecommerce.db            # generated by setup_db.py
├── core/
│   ├── schema_manager.py       # tables/columns/relationships + metric registry + live DB introspection
│   ├── prompt_builder.py       # builds the LLM system prompt from the live schema/DB
│   ├── reasoner.py             # Reasoner interface: GeminiReasoner / OpenRouterReasoner / OpenAIReasoner / ClaudeReasoner / RuleBasedReasoner
│   ├── intent_parser.py        # rule-based reasoner: text -> structured Intent
│   ├── ambiguity_detector.py   # rule-based reasoner: Intent -> "is this safe to answer?"
│   ├── clarification_engine.py # rule-based reasoner: AmbiguityReport -> UI payload
│   ├── intent_resolution.py    # rule-based reasoner: merges a clarification answer into the pending Intent
│   ├── sql_generator.py        # rule-based reasoner: Intent -> SQL
│   ├── context_manager.py      # per-session conversation transcript + query history (used by all reasoners)
│   ├── sql_validator.py        # DETERMINISTIC: read-only/single-statement/known-table enforcement
│   ├── db_executor.py          # DETERMINISTIC: executes via a read-only SQLite connection
│   ├── result_formatter.py     # shapes rows for the UI + a one-line summary
│   └── query_handler.py        # orchestrator: Reasoner -> Validator -> Executor -> Formatter
├── static/
│   └── index.html              # chat UI, schema explorer, SQL + results panel, active-reasoner badge
└── tests/
    ├── test_pipeline.py        # 30 checks, offline, proves the safety boundary
    └── test_llm_smoke.py       # optional live check of the real LLM backend
```

## Extending the system

- **Give the LLM a new metric** → add a `Metric(...)` entry to
  `SchemaManager.metrics` in `schema_manager.py` (id, label, keywords,
  `sql_expression`, `join_hint`). Both the rule-based reasoner AND the
  LLM's system prompt (built from the same registry) pick it up
  automatically — no other file changes.
- **Add a new filterable column** → add it to `SchemaManager.filterable_columns`;
  its live values are automatically included in the LLM's grounding data and
  the rule-based reasoner's `resolve_filter_value()` check.
- **Swap the LLM provider** → set `REASONER=gemini`, `REASONER=openrouter`,
  `REASONER=openai`, or `REASONER=claude` (or add a new
  `Reasoner` subclass for another provider); nothing in `query_handler.py`,
  the validator, or the executor needs to change, by design.
