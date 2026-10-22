"""
clarification_engine.py
------------------------
MODULE: Clarification Engine

Earlier versions of this project kept a Python dict mapping a fixed set of
ambiguity-type strings to pre-written question text (one template per demo
case). That's exactly the "blindly reply with pre-written info" pattern
this project is trying to avoid.

Now the reasoning happens once, in ambiguity_detector.py: the detector
looks at the actual schema and the actual database content and composes
`report.question` / `report.reason` / `report.options` FROM that live
state (real metric labels, real DB values, real numeric column names) --
so a brand new ambiguity_type this module has never seen before (say,
"RANKING_METRIC::warehouse" once a new entity is added to the schema)
still gets a correct, specific question with zero changes here.

This module's only remaining job is packaging that already-composed
reasoning into the JSON shape the UI expects, with a defensive fallback in
case a future detector branch forgets to set `question`.
"""

from core.ambiguity_detector import AmbiguityReport


def generate(report: AmbiguityReport) -> dict:
    """Returns a JSON-serialisable clarification payload for the UI."""
    return {
        "question": report.question or "Could you clarify what you mean?",
        "reason": report.reason,
        "options": report.options,
        "ambiguity_type": report.ambiguity_type,
        "missing_slot": report.missing_slot,
    }
