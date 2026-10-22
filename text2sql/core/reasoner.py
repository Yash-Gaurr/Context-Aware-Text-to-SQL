"""
reasoner.py
------------
MODULE: Intent & Ambiguity Detector + Clarification Engine + Text-to-SQL
        Generator -- LLM-backed implementation, behind a swappable interface.

THE SAFETY BOUNDARY OF THIS PROJECT LIVES AT THIS FILE'S RETURN VALUE.

A Reasoner's only contract is: given the conversation so far, return either
a clarification to show the user, or a SQL string to attempt. That SQL
string is NEVER trusted. query_handler.py always runs it through
sql_validator.py (deterministic: read-only, single-statement, known-tables
only) and db_executor.py (deterministic: read-only SQLite connection)
before any row of data is touched -- regardless of which Reasoner produced
it. This is what "LLM for semantic reasoning + deterministic layer for
safe execution" means concretely in this codebase: the LLM is upstream of
a hard, code-enforced gate it cannot talk its way past.

Five interchangeable implementations are provided:

  GeminiReasoner     -- FREE tier, no credit card. Google's Gemini API via
                      the official google-genai SDK. The recommended
                      default for anyone without a paid API budget.
  OpenRouterReasoner -- FREE tier, no credit card. Routes to whichever
                      open-weight model is currently offered free (model
                      IDs ending in ':free') through an OpenAI-compatible
                      endpoint. Free model availability rotates over time.
  OpenAIReasoner     -- paid. Calls OpenAI's Chat Completions API with the
                      schema/metric/live-data-grounded system prompt from
                      prompt_builder.py, in JSON mode.
  ClaudeReasoner     -- paid. Same contract, via Anthropic's Messages API.
  RuleBasedReasoner  -- no API key or network required at all. Wraps the
                      earlier, hand-written schema-grounded reasoning
                      engine (intent_parser + ambiguity_detector +
                      clarification_engine + sql_generator) behind the
                      exact same interface, so the project still runs
                      fully offline and every strategy can be compared
                      side by side.

Which one is active is chosen once, in app.py, from environment variables
-- nothing else in the pipeline needs to know or care which it is.
"""

import json
import os
import re
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple


class ReasonerError(Exception):
    """Raised when the reasoning layer cannot be reached, isn't configured,
    or returns something that can't be trusted/parsed. Callers must treat
    this as 'no SQL was produced' -- never as license to execute anything."""


class Reasoner(ABC):
    @abstractmethod
    def reason(self, session_id: str, conversation: List[Dict[str, str]]) -> dict:
        """conversation: the full transcript so far, in chronological order,
        as [{"role": "user"|"assistant", "content": str}, ...].

        Must return EXACTLY ONE of:
          {"type": "clarification", "question": str, "options": [{"id": str, "label": str}, ...], "reason": str}
          {"type": "sql", "sql": str, "explanation": str}
        """
        raise NotImplementedError

    def label(self) -> str:
        return self.__class__.__name__


# ---------------------------------------------------------------------------
def normalize_options(raw_options) -> List[dict]:
    """Clarification options must reach the UI as {"id", "label"} objects
    (that's what static/index.html renders as buttons). Defensively accept
    either that shape OR a bare list of strings -- regardless of what the
    system prompt asked for, a model can drift from the instructed schema,
    and normalizing here means the UI never silently renders an empty
    button no matter which reasoner produced the response."""
    normalized = []
    for opt in raw_options or []:
        if isinstance(opt, dict):
            label = str(opt.get("label") or opt.get("id") or "").strip()
            oid = str(opt.get("id") or opt.get("label") or "").strip()
            if label or oid:
                normalized.append({"id": oid or label, "label": label or oid})
        else:
            text = str(opt).strip()
            if text:
                normalized.append({"id": text, "label": text})
    return normalized


# ---------------------------------------------------------------------------
def _parse_json_decision(raw_text: str) -> dict:
    """Shared, defensive JSON parsing for every LLM backend: strips stray
    markdown code fences some models add despite instructions, validates
    the two allowed shapes, and normalizes clarification options to the
    {"id","label"} object shape the UI actually renders -- before anything
    downstream trusts any of it."""
    cleaned = re.sub(r"^```(json)?|```$", "", raw_text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ReasonerError(f"Reasoner did not return valid JSON: {raw_text[:200]!r}") from exc

    kind = data.get("type")
    if kind == "clarification":
        if not data.get("question"):
            raise ReasonerError("Clarification response is missing 'question'.")
        data["options"] = normalize_options(data.get("options"))
        data.setdefault("reason", "")
        return data
    if kind == "sql":
        if not str(data.get("sql", "")).strip():
            raise ReasonerError("SQL response is missing 'sql'.")
        data.setdefault("explanation", "")
        return data
    raise ReasonerError(f"Unrecognised response type from reasoner: {kind!r}")


# ---------------------------------------------------------------------------
class OpenAIReasoner(Reasoner):
    """Default reasoning backend. Uses OpenAI's Chat Completions API in JSON
    mode. Requires OPENAI_API_KEY; model is configurable via TEXT2SQL_MODEL
    (defaults to a small, cost-effective model -- this workload is simple
    structured reasoning, not a task that needs a flagship model)."""

    def __init__(self, model: Optional[str] = None):
        try:
            import openai
        except ImportError as exc:
            raise ReasonerError("The 'openai' package is not installed. Run: pip install openai") from exc
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ReasonerError("OPENAI_API_KEY is not set. Export your OpenAI API key before starting the app.")
        self._client = openai.OpenAI(api_key=api_key)
        self.model = model or os.environ.get("TEXT2SQL_MODEL", "gpt-5-mini")
        from core.prompt_builder import build_system_prompt
        self._system_prompt = build_system_prompt()

    def label(self) -> str:
        return f"OpenAI ({self.model})"

    def reason(self, session_id: str, conversation: List[Dict[str, str]]) -> dict:
        messages = [{"role": "system", "content": self._system_prompt}] + conversation
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            raise ReasonerError(f"OpenAI API call failed: {exc}") from exc

        text = response.choices[0].message.content or ""
        return _parse_json_decision(text)


# ---------------------------------------------------------------------------
class GeminiReasoner(Reasoner):
    """FREE reasoning backend -- recommended if you don't have a paid API
    budget. Uses Google's Gemini API (generous free tier: no credit card,
    roughly 15 requests/min and 1500/day on Flash models as of writing --
    verify current limits at ai.google.dev/gemini-api/docs/rate-limits,
    since Google can change them).

    Requires GEMINI_API_KEY (get one free at https://aistudio.google.com/apikey).
    Model defaults to 'gemini-flash-latest', a Google-maintained alias that
    always points at the current recommended Flash model, so this doesn't
    go stale as Google ships new versions."""

    def __init__(self, model: Optional[str] = None):
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise ReasonerError(
                "The 'google-genai' package is not installed. Run: pip install google-genai"
            ) from exc
        api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
        if not api_key:
            raise ReasonerError(
                "GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey "
                "and export it before starting the app."
            )
        self._genai = genai
        self._types = types
        self._client = genai.Client(api_key=api_key)
        self.model = model or os.environ.get("TEXT2SQL_MODEL", "gemini-3.5-flash-lite")
        from core.prompt_builder import build_system_prompt
        self._system_prompt = build_system_prompt()

    def label(self) -> str:
        return f"Gemini ({self.model}, free tier)"

    def reason(self, session_id: str, conversation: List[Dict[str, str]]) -> dict:
        # Gemini's roles are "user" / "model" (not "assistant"), and the
        # system prompt is passed separately via GenerateContentConfig
        # rather than as a message in the list.
        contents = [
            {"role": "model" if turn["role"] == "assistant" else "user",
             "parts": [{"text": turn["content"]}]}
            for turn in conversation
        ]

        config_kwargs = {
            "system_instruction": self._system_prompt,
            "response_mime_type": "application/json",
            "max_output_tokens": int(os.environ.get("TEXT2SQL_MAX_OUTPUT_TOKENS", "2048")),
        }
        # Only models with reasoning / thinking support thinking_budget
        if "thinking" in self.model.lower() or "2.5" in self.model or "3.7" in self.model:
            try:
                config_kwargs["thinking_config"] = self._types.ThinkingConfig(thinking_budget=0)
            except AttributeError:
                pass

        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=contents,
                config=self._types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as exc:
            # If 400 INVALID_ARGUMENT because of thinking_config, retry without it
            if "thinking_config" in config_kwargs and "INVALID_ARGUMENT" in str(exc):
                config_kwargs.pop("thinking_config", None)
                try:
                    response = self._client.models.generate_content(
                        model=self.model,
                        contents=contents,
                        config=self._types.GenerateContentConfig(**config_kwargs),
                    )
                except Exception as exc2:
                    exc = exc2
            # If model experiences 503 UNAVAILABLE (high demand), fall back to gemini-3.5-flash-lite
            if ("503" in str(exc) or "UNAVAILABLE" in str(exc)) and self.model != "gemini-3.5-flash-lite":
                print(f"[warning] {self.model} is experiencing high demand (503). Retrying with gemini-3.5-flash-lite...")
                self.model = "gemini-3.5-flash-lite"
                config_kwargs.pop("thinking_config", None)
                try:
                    response = self._client.models.generate_content(
                        model=self.model,
                        contents=contents,
                        config=self._types.GenerateContentConfig(**config_kwargs),
                    )
                except Exception as exc3:
                    raise ReasonerError(f"Gemini API call failed: {exc3}") from exc3
            else:
                if "response" not in locals():
                    raise ReasonerError(f"Gemini API call failed: {exc}") from exc

        finish_reason = None
        try:
            finish_reason = response.candidates[0].finish_reason
        except Exception:
            pass

        text = (response.text or "").strip()
        if not text:
            raise ReasonerError(
                f"Gemini returned an empty response (finish_reason={finish_reason}). "
                f"If this persists, try increasing TEXT2SQL_MAX_OUTPUT_TOKENS."
            )
        try:
            return _parse_json_decision(text)
        except ReasonerError as exc:
            if finish_reason is not None and str(finish_reason).upper() not in ("STOP", "1"):
                raise ReasonerError(
                    f"{exc} (finish_reason={finish_reason} -- the response likely got cut off "
                    f"before completing; try increasing TEXT2SQL_MAX_OUTPUT_TOKENS)"
                ) from exc
            raise


# ---------------------------------------------------------------------------
class OpenRouterReasoner(Reasoner):
    """FREE reasoning backend -- routes to whichever open-weight model is
    currently offered at $0/token on OpenRouter (model IDs ending in
    ':free'). No credit card required. Free-tier limits as of writing:
    20 requests/minute, 50/day on an unfunded account (1000/day if you've
    ever added $10 of credit) -- verify current numbers at
    openrouter.ai/docs, and current free model IDs at
    openrouter.ai/models?max_price=0, since which specific models are free
    rotates over time and TEXT2SQL_MODEL should be updated to match.

    Implemented as a thin wrapper around the OpenAI SDK pointed at
    OpenRouter's OpenAI-compatible endpoint -- OpenRouter deliberately
    mirrors the OpenAI API shape, so no separate client library is needed."""

    def __init__(self, model: Optional[str] = None):
        try:
            import openai
        except ImportError as exc:
            raise ReasonerError("The 'openai' package is not installed. Run: pip install openai") from exc
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ReasonerError(
                "OPENROUTER_API_KEY is not set. Get a free key (no card needed) at "
                "https://openrouter.ai/keys and export it before starting the app."
            )
        self._client = openai.OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
        self.model = model or os.environ.get("TEXT2SQL_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
        from core.prompt_builder import build_system_prompt
        self._system_prompt = build_system_prompt()

    def label(self) -> str:
        return f"OpenRouter ({self.model}, free tier)"

    def reason(self, session_id: str, conversation: List[Dict[str, str]]) -> dict:
        messages = [{"role": "system", "content": self._system_prompt}] + conversation
        # Not every free/open-weight model on OpenRouter honours
        # response_format reliably -- try JSON mode first, and if the API
        # rejects that parameter for this particular model, retry once
        # without it (the shared JSON-shape validation in
        # _parse_json_decision still guards the output either way).
        try:
            response = self._client.chat.completions.create(
                model=self.model, messages=messages, response_format={"type": "json_object"},
            )
        except Exception:
            try:
                response = self._client.chat.completions.create(model=self.model, messages=messages)
            except Exception as exc:
                raise ReasonerError(f"OpenRouter API call failed: {exc}") from exc

        text = response.choices[0].message.content or ""
        return _parse_json_decision(text)


# ---------------------------------------------------------------------------
class ClaudeReasoner(Reasoner):
    """Alternate reasoning backend using Anthropic's Messages API. Same
    contract as OpenAIReasoner -- swap REASONER=claude (see app.py) to use
    this instead, no other code changes needed anywhere in the pipeline."""

    def __init__(self, model: Optional[str] = None):
        try:
            import anthropic
        except ImportError as exc:
            raise ReasonerError("The 'anthropic' package is not installed. Run: pip install anthropic") from exc
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ReasonerError("ANTHROPIC_API_KEY is not set. Export your Anthropic API key before starting the app.")
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model or os.environ.get("TEXT2SQL_MODEL", "claude-sonnet-5")
        from core.prompt_builder import build_system_prompt
        self._system_prompt = build_system_prompt()

    def label(self) -> str:
        return f"Claude ({self.model})"

    def reason(self, session_id: str, conversation: List[Dict[str, str]]) -> dict:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=self._system_prompt,
                messages=conversation,
            )
        except Exception as exc:
            raise ReasonerError(f"Claude API call failed: {exc}") from exc

        text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text").strip()
        return _parse_json_decision(text)


# ---------------------------------------------------------------------------
class RuleBasedReasoner(Reasoner):
    """Deterministic, offline fallback -- no API key, no network. Wraps the
    hand-written schema-grounded reasoning engine built earlier in this
    project (intent_parser -> ambiguity_detector -> clarification_engine ->
    sql_generator) behind the same Reasoner contract as the LLM backends,
    so it's a genuine drop-in comparison rather than a degraded stand-in.

    Unlike the LLM backends, this class needs real structured state between
    turns (an Intent object, not just raw text), so -- and only here -- it
    keeps its own small per-session pending-question store rather than
    relying on ContextManager's plain transcript."""

    def __init__(self):
        self._pending: Dict[str, tuple] = {}  # session_id -> (Intent, AmbiguityReport)

    def label(self) -> str:
        return "Rule-based (offline, no API key required)"

    def reason(self, session_id: str, conversation: List[Dict[str, str]]) -> dict:
        from core import intent_parser, ambiguity_detector, clarification_engine, sql_generator
        from core.intent_resolution import resolve_answer

        user_text = conversation[-1]["content"] if conversation else ""

        if session_id in self._pending:
            intent, ambiguity = self._pending.pop(session_id)
            intent = resolve_answer(intent, ambiguity, user_text)
        else:
            intent = intent_parser.parse(user_text)

        remaining = ambiguity_detector.detect(intent)
        if remaining:
            self._pending[session_id] = (intent, remaining)
            payload = clarification_engine.generate(remaining)
            return {"type": "clarification", **payload}

        try:
            generated = sql_generator.generate(intent)
        except ValueError as exc:
            raise ReasonerError(str(exc))
        return {
            "type": "sql",
            "sql": generated["display_sql"],
            "explanation": f"Resolved intent: entity={intent.target_entity}, metric={intent.metric}, "
                            f"filters={intent.filters}, time_range={intent.time_range}",
        }


# ---------------------------------------------------------------------------
def get_reasoner(preference: Optional[str] = None) -> Tuple[Reasoner, Optional[str]]:
    """Factory used by app.py. `preference` is 'gemini' | 'openrouter' |
    'openai' | 'claude' | 'rule' | 'auto'. 'auto' (the default) tries the
    FREE backends first (Gemini, then OpenRouter), then the paid ones, then
    finally falls back to the offline rule-based reasoner so the app always
    starts regardless of what's configured. Returns (reasoner, warning_or_None)."""
    preference = (preference or os.environ.get("REASONER", "auto")).lower()

    explicit = {
        "rule": RuleBasedReasoner,
        "gemini": GeminiReasoner,
        "openrouter": OpenRouterReasoner,
        "openai": OpenAIReasoner,
        "claude": ClaudeReasoner,
    }
    if preference in explicit:
        if preference == "rule":
            return RuleBasedReasoner(), None
        try:
            return explicit[preference](), None
        except ReasonerError as exc:
            return RuleBasedReasoner(), (
                f"Requested reasoner '{preference}' failed to initialize: {exc}. "
                "Falling back to offline rule-based reasoner."
            )

    # auto: free backends first (no billing surprises), then paid ones
    for cls in (GeminiReasoner, OpenRouterReasoner, OpenAIReasoner, ClaudeReasoner):
        try:
            return cls(), None
        except ReasonerError:
            continue
    return RuleBasedReasoner(), (
        "No GEMINI_API_KEY, OPENROUTER_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY found -- "
        "running with the offline rule-based reasoner. Set one of those environment variables "
        "(GEMINI_API_KEY is free and easiest to get) and restart to use the LLM-backed layer."
    )
