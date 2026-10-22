"""
intent_resolution.py
----------------------
MODULE: (part of) Conversation / Context Manager -- rule-based path only

Pure function: given a pending Intent, the AmbiguityReport that was asked
about it, and the user's free-text/clicked answer, fill in the one missing
slot and return the updated Intent. Kept as a standalone function (rather
than a method on ContextManager) because RuleBasedReasoner is the only
caller now that ContextManager itself is just conversation-transcript
storage (see context_manager.py) -- the LLM-backed reasoner does this same
job implicitly, by reading the transcript, so this logic is specific to
the deterministic fallback path.
"""

from datetime import timedelta
from core.ambiguity_detector import AmbiguityReport
from core.intent_parser import Intent, REFERENCE_TODAY


def resolve_answer(intent: Intent, ambiguity: AmbiguityReport, user_answer: str) -> Intent:
    answer_raw = user_answer.strip()
    answer = answer_raw.lower()

    chosen_id = None

    # 1. Exact match against an offered option's id or label.
    for option in ambiguity.options:
        if option["id"].lower() == answer or option["label"].lower() == answer:
            chosen_id = option["id"]
            break

    # 2. Loose containment match ("revenue please" contains "revenue").
    if chosen_id is None:
        for option in ambiguity.options:
            if option["id"].lower() in answer or option["label"].lower() in answer:
                chosen_id = option["id"]
                break

    # 3. Common free-phrasing synonyms -- only applied for ids that were
    #    actually among the options on offer, so this never invents a
    #    resolution the detector didn't present.
    if chosen_id is None:
        offered_ids = {o["id"] for o in ambiguity.options}
        synonym_hints = {
            "revenue": ["revenue", "money", "sales"],
            "orders_count": ["order"],
            "quantity": ["unit", "sold", "quantity"],
            "price": ["price", "cheap", "expensive", "cost"],
            "7": ["7", "week"],
            "30": ["30", "month"],
            "all": ["all"],
        }
        for candidate_id, words in synonym_hints.items():
            if candidate_id in offered_ids and any(w in answer for w in words):
                chosen_id = candidate_id
                break

    # 4. Nothing matched an offered option -- treat the raw typed text as
    #    the value itself (covers a free-typed answer to an open-ended
    #    fallback question, e.g. a numeric-column name).
    if chosen_id is None:
        chosen_id = answer_raw

    slot = ambiguity.missing_slot
    if slot == "metric":
        intent.metric = chosen_id
    elif slot == "time_range":
        if chosen_id == "7":
            start = REFERENCE_TODAY - timedelta(days=7)
            intent.time_range = [start.isoformat(), REFERENCE_TODAY.isoformat()]
            intent.time_phrase = "last 7 days"
        elif chosen_id == "30":
            start = REFERENCE_TODAY - timedelta(days=30)
            intent.time_range = [start.isoformat(), REFERENCE_TODAY.isoformat()]
            intent.time_phrase = "last 30 days"
        else:
            intent.time_range = None
            intent.time_phrase = None  # "all time" -> no filter
    elif slot == "target_entity":
        intent.target_entity = chosen_id
        intent.unresolved = False
    elif slot.startswith("filter:"):
        key = slot.split(":", 1)[1]
        if chosen_id == "__drop__":
            intent.filters.pop(key, None)
        else:
            intent.filters[key] = chosen_id

    return intent
