"""
ambiguity_detector.py
----------------------
MODULE: Intent & Ambiguity Detector (ambiguity half)

THIS IS THE HEART OF THE PROJECT -- and it is deliberately built as a small
REASONING ENGINE, not a lookup table of pre-written questions.

The detector never asks "does the text say best/top/recent" and jump to a
canned response. Instead, for each check it asks a structural question of
the Intent against the SCHEMA (and, for filters, the live DATABASE), and
only decides to interrupt the user when the answer to that structural
question is genuinely undetermined:

  Check                  Structural question asked
  ----------------------------------------------------------------------
  Ranking / superlative   "How many measurable metrics does the SCHEMA
                           register for this entity?"
                             0  -> nothing to rank by; fall back to
                                   introspecting numeric columns directly
                             1  -> only one sensible reading exists;
                                   resolve it silently, don't interrupt
                             2+ -> genuinely ambiguous; ask, using
                                   whichever metrics are ACTUALLY
                                   registered (not a fixed pair)

  Temporal reference      "Did the parser recognise a time reference that
                           did NOT resolve to a concrete date range?"
                           (generic on intent state, not string-matched
                           against the word "recent" specifically)

  Filter values            "Does this value the user typed actually exist
                           in the DATABASE right now?" -- queried live via
                           SchemaManager.resolve_filter_value(). Zero
                           matches -> ask, using the real values that DO
                           exist. Multiple close matches -> ask which one.
                           Exactly one plausible match -> silently correct
                           it (e.g. typo "Puna" -> "Pune") and move on.

Because every branch reasons off SchemaManager (the metric registry, the
numeric-column introspection, the live distinct-value lookup) rather than
a Python dict keyed by exact phrase, extending the schema (a new entity, a
new metric, a new filterable column) automatically extends what the
detector can reason about -- no new branches required here.
"""

import difflib
from dataclasses import dataclass
from typing import Optional, List
from core.intent_parser import Intent
from core.schema_manager import SchemaManager

_schema = SchemaManager()


@dataclass
class AmbiguityReport:
    ambiguity_type: str          # machine-readable code (may be dynamic, e.g. "RANKING_METRIC::product")
    missing_slot: str            # which Intent field/sub-key is unresolved, e.g. "metric" or "filter:city"
    reason: str                  # WHY this is ambiguous, composed from the live reasoning state
    question: str                # the actual question to show, composed from the live reasoning state
    options: List[dict]          # [{"id": ..., "label": ...}, ...] -- built from real schema/DB data


def _join_labels(labels: List[str]) -> str:
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} or {labels[1]}"
    return ", ".join(labels[:-1]) + f", or {labels[-1]}"


def detect(intent: Intent) -> Optional[AmbiguityReport]:
    """Returns None if the intent is unambiguous enough to generate SQL,
    otherwise an AmbiguityReport describing what to ask. May also mutate
    `intent` in place when a check resolves something silently (e.g. only
    one metric was possible, or a filter typo had exactly one real match)
    -- that is the detector *reasoning its way past* a non-issue rather
    than needlessly asking about it."""

    # --- Check 1: is there even a recognised entity to reason about? -------
    if intent.unresolved:
        entities = [t[:-1] for t in _schema.table_names() if t != "order_items"]
        options = [{"id": e, "label": e.capitalize() + "s"} for e in entities]
        return AmbiguityReport(
            ambiguity_type="UNKNOWN_ENTITY",
            missing_slot="target_entity",
            reason="The question doesn't clearly refer to any part of the database I know about.",
            question=f"Are you asking about {_join_labels([o['label'].lower() for o in options])}?",
            options=options,
        )

    # --- Check 2: ranking / superlative question without a chosen metric ---
    if intent.superlative and intent.metric is None:
        candidates = _schema.metrics_for(intent.target_entity)

        if len(candidates) == 1:
            # Only one measurable quantity is registered for this entity --
            # there is nothing to actually disambiguate, so resolve it and
            # move on instead of interrupting the user unnecessarily.
            intent.metric = candidates[0].id

        elif len(candidates) >= 2:
            labels = [m.label for m in candidates]
            return AmbiguityReport(
                ambiguity_type=f"RANKING_METRIC::{intent.target_entity}",
                missing_slot="metric",
                reason=(f"'{intent.raw_query}' ranks {intent.target_entity}s, but the schema registers "
                        f"{len(candidates)} different measurable quantities for {intent.target_entity}s "
                        f"({', '.join(labels)}) -- each gives a different SQL query and a different answer."),
                question=f"Do you want to rank by {_join_labels(labels)}?",
                options=[{"id": m.id, "label": m.label} for m in candidates],
            )

        else:
            # No Metric is registered for this entity at all -- fall back to
            # reasoning directly from the schema's column types instead of
            # giving up. This is what lets the system handle an entity
            # nobody explicitly wired a metric for.
            numeric_cols = _schema.numeric_columns(intent.target_entity)
            if numeric_cols:
                return AmbiguityReport(
                    ambiguity_type=f"RANKING_METRIC::{intent.target_entity}",
                    missing_slot="metric",
                    reason=(f"No standard ranking metric is registered for {intent.target_entity}s, "
                            f"but the schema has these numeric column(s) that could define 'best': "
                            f"{', '.join(numeric_cols)}."),
                    question=f"Which field should decide '{intent.raw_query.split()[0] if intent.raw_query else 'best'}' "
                              f"for {intent.target_entity}s -- {_join_labels(numeric_cols)}?",
                    options=[{"id": c, "label": c.replace('_', ' ')} for c in numeric_cols],
                )
            return AmbiguityReport(
                ambiguity_type="NO_RANKABLE_FIELD",
                missing_slot="metric",
                reason=f"There is no numeric field on {intent.target_entity}s that a ranking could be based on.",
                question=f"I don't have any measurement to rank {intent.target_entity}s by -- "
                         f"could you tell me what to compare them on?",
                options=[],
            )

    # --- Check 3: a time reference was seen but never resolved to a range --
    # Generic on Intent STATE (phrase set, range missing), not on the
    # specific word "recent" -- any future vague-time word added to the
    # parser's vocabulary is covered by this same check automatically.
    if intent.time_phrase and intent.time_range is None:
        return AmbiguityReport(
            ambiguity_type="TIME_RANGE",
            missing_slot="time_range",
            reason=(f'"{intent.time_phrase}" doesn\'t correspond to one specific date range, '
                    f"and the result set would differ a lot depending on the window chosen."),
            question=f'What time window do you mean by "{intent.time_phrase}"?',
            options=[
                {"id": "7", "label": "Last 7 days"},
                {"id": "30", "label": "Last 30 days"},
                {"id": "all", "label": "All time"},
            ],
        )

    # --- Check 4: filter values, grounded against the LIVE database --------
    # For every filter the parser extracted as raw text, ask the schema
    # what actually exists in that column right now. This is real reasoning
    # against the database's current content, not a hardcoded value list --
    # a typo like "Puna" gets corrected via fuzzy match; a value with no
    # real match at all triggers a clarification built from real data.
    for key, raw_value in list(intent.filters.items()):
        if key not in _schema.filterable_columns:
            continue
        resolved, alternatives = _schema.resolve_filter_value(key, raw_value)
        if resolved is not None:
            intent.filters[key] = resolved  # silently corrected/confirmed against real data
            continue

        if not alternatives:
            return AmbiguityReport(
                ambiguity_type=f"UNKNOWN_FILTER_VALUE::{key}",
                missing_slot=f"filter:{key}",
                reason=f'I checked the database and there is no {key} matching "{raw_value}".',
                question=f'I don\'t have any records with {key} "{raw_value}". '
                         f'Did you mean it to be something else, or should I drop that filter?',
                options=[{"id": "__drop__", "label": f"Ignore the {key} filter"}],
            )

        return AmbiguityReport(
            ambiguity_type=f"AMBIGUOUS_FILTER_VALUE::{key}",
            missing_slot=f"filter:{key}",
            reason=f'"{raw_value}" is close to more than one {key} actually in the database.',
            question=f'Which {key} did you mean -- {_join_labels(alternatives)}?',
            options=[{"id": v, "label": v} for v in alternatives],
        )

    return None
