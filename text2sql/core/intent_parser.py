"""
intent_parser.py
-----------------
MODULE: (part of) Intent & Ambiguity Detector

Turns a raw natural-language question into a structured, *partial* Intent
object. "Partial" is the key word: some slots may be left as None when the
question genuinely does not specify them (e.g. "best customer" does not say
whether "best" means most orders or most revenue). It is the Ambiguity
Detector's job, not this module's, to decide whether a missing slot is a
problem.

Two things are deliberately NOT hardcoded here, and are instead pulled from
SchemaManager (i.e. from the database's own structure/content):

  1. Metric vocabulary ("revenue" means customer metric X) comes from
     SchemaManager.metrics_for(entity) -- see schema_manager.py's Metric
     registry -- not from a per-entity dict living in this file.
  2. Filter values (city/category names) are only ever extracted as RAW
     candidate text here. Whether "Puna" is valid, and what it should be
     corrected to, is decided later by the Ambiguity Detector querying the
     live database (SchemaManager.resolve_filter_value) -- this file never
     hardcodes "the list of valid cities".

This keeps the parser itself reduced to genuinely stable linguistic
patterns (superlative words, aggregation triggers, time phrases) while
anything that could change as the *data* changes stays grounded in the
schema/database instead of Python source.
"""

import re
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from typing import Optional, List, Dict, Any

from core.schema_manager import SchemaManager

REFERENCE_TODAY = date(2026, 9, 7)  # kept in sync with database/setup_db.py

_schema = SchemaManager()


@dataclass
class Intent:
    raw_query: str
    target_entity: Optional[str] = None       # "customer" | "product" | "order"
    action: str = "SELECT"
    metric: Optional[str] = None              # e.g. "revenue" | "orders_count" | "quantity" | "price"
    aggregation: Optional[str] = None         # "MAX" | "SUM" | "AVG" | "COUNT" | "MIN"
    superlative: bool = False                 # any ranking word seen ("best", "cheapest", "worst", ...)
    sort_direction: str = "DESC"              # "DESC" for best/top/highest, "ASC" for cheapest/worst/lowest
    time_phrase: Optional[str] = None         # raw phrase e.g. "last month"
    time_range: Optional[List[str]] = None    # [start_date, end_date] once resolved
    filters: Dict[str, str] = field(default_factory=dict)   # e.g. {"city": "Puna"} -- RAW, not yet validated
    group_by: Optional[str] = None            # e.g. "category"
    limit: int = 100
    unresolved: bool = False                  # true while parser couldn't find an entity at all

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Vocabulary that genuinely IS stable language, not data (extend freely --
# note that metric words and filter values are NOT here; see module docstring)
# ---------------------------------------------------------------------------

ENTITY_KEYWORDS = {
    "customer": ["customer", "customers", "client", "clients", "buyer", "buyers"],
    "product": ["product", "products", "item", "items"],
    "order": ["order", "orders", "purchase", "purchases"],
}

# Any of these mark "this is a ranking/superlative question" -- the
# DIRECTION (top-of-list vs bottom-of-list) is decided separately below.
DESCENDING_WORDS = ["best", "top", "most valuable", "highest performing",
                     "greatest", "leading", "number one", "highest", "most",
                     "priciest", "most expensive"]
ASCENDING_WORDS = ["cheapest", "lowest", "least", "worst", "fewest", "smallest", "bottom"]
SUPERLATIVE_WORDS = DESCENDING_WORDS + ASCENDING_WORDS

COUNT_TRIGGERS = ["how many", "count of", "number of"]
SUM_TRIGGERS = ["total", "sum of"]
AVG_TRIGGERS = ["average", "avg"]

TIME_PHRASE_PATTERNS = [
    "this month", "last month", "this week", "last week",
    "this year", "last year", "today", "yesterday",
]

# Any word here signals "a time reference exists" without saying which
# concrete window is meant. The detector's rule is generic (phrase set,
# range unresolved => ask) so growing this list needs no detector changes.
VAGUE_TIME_WORDS = ["recent", "recently", "lately", "currently", "these days", "latest"]

# A raw filter value has no explicit closing delimiter in plain English, so
# both patterns capture NON-GREEDILY and stop as soon as they hit another
# recognised entity word, punctuation, or the end of the string -- this is
# what stops "category Home orders" from swallowing "orders" into the
# category value. (Without this, "Home orders" would be captured whole and
# fail live-DB validation for a reason unrelated to the real typo case.)
_ENTITY_STOP_WORDS = r"(?:customers?|clients?|buyers?|products?|items?|orders?|purchases?)"
CITY_FILTER_RE = re.compile(
    rf"\b(?:in|from)\s+(?!category\b)([A-Za-z][A-Za-z\s]*?)(?=\s+{_ENTITY_STOP_WORDS}\b|[.,!?]|$)"
)
CATEGORY_FILTER_RE = re.compile(
    rf"\bcategory\s+([A-Za-z][A-Za-z\s]*?)(?=\s+{_ENTITY_STOP_WORDS}\b|[.,!?]|$)"
)
LIMIT_RE = re.compile(r"\btop\s+(\d+)\b")


def _resolve_time_phrase(phrase: str) -> Optional[List[str]]:
    """Turn a recognised relative time phrase into a concrete [start, end] date range,
    anchored on REFERENCE_TODAY so results are reproducible."""
    today = REFERENCE_TODAY
    if phrase == "today":
        return [today.isoformat(), today.isoformat()]
    if phrase == "yesterday":
        d = today - timedelta(days=1)
        return [d.isoformat(), d.isoformat()]
    if phrase == "this week":
        start = today - timedelta(days=today.weekday())
        return [start.isoformat(), today.isoformat()]
    if phrase == "last week":
        start = today - timedelta(days=today.weekday() + 7)
        end = start + timedelta(days=6)
        return [start.isoformat(), end.isoformat()]
    if phrase == "this month":
        start = today.replace(day=1)
        return [start.isoformat(), today.isoformat()]
    if phrase == "last month":
        first_of_this_month = today.replace(day=1)
        last_month_end = first_of_this_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return [last_month_start.isoformat(), last_month_end.isoformat()]
    if phrase == "this year":
        return [today.replace(month=1, day=1).isoformat(), today.isoformat()]
    if phrase == "last year":
        return [date(today.year - 1, 1, 1).isoformat(), date(today.year - 1, 12, 31).isoformat()]
    return None


def parse(raw_query: str) -> Intent:
    """Entry point: raw text -> partial Intent. No slot is guessed here; a
    slot is only filled when the text actually supports it."""
    text = raw_query.strip().lower()
    intent = Intent(raw_query=raw_query)

    # 1. Target entity ----------------------------------------------------
    for entity, keywords in ENTITY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            intent.target_entity = entity
            break
    if intent.target_entity is None:
        intent.unresolved = True
        return intent

    # 2. Superlative + direction --------------------------------------------
    # "best"/"top"/"highest" -> rank DESCENDING; "cheapest"/"worst"/"lowest"
    # -> rank ASCENDING. Direction is a separate slot from *which* metric,
    # because e.g. "cheapest product" and "priciest product" both rank by
    # the price metric, just in opposite directions.
    intent.superlative = any(w in text for w in SUPERLATIVE_WORDS)
    intent.sort_direction = "ASC" if any(w in text for w in ASCENDING_WORDS) else "DESC"

    # 3. Metric -- sourced from the schema's metric registry for this entity,
    #    NOT from a hardcoded per-entity dict in this file. Whatever metrics
    #    SchemaManager knows about for this entity are the only ones that can
    #    be recognised here; adding a Metric to the schema is enough to teach
    #    the parser its keywords too.
    for metric in _schema.metrics_for(intent.target_entity):
        if any(kw in text for kw in metric.keywords):
            intent.metric = metric.id
            break

    # 4. Aggregation function -----------------------------------------------
    if any(kw in text for kw in COUNT_TRIGGERS):
        intent.aggregation = "COUNT"
    elif any(kw in text for kw in SUM_TRIGGERS):
        intent.aggregation = "SUM"
    elif any(kw in text for kw in AVG_TRIGGERS):
        intent.aggregation = "AVG"
    elif intent.superlative:
        intent.aggregation = "MAX"  # resolved further once metric is known

    # 5. Time phrase --------------------------------------------------------
    for phrase in TIME_PHRASE_PATTERNS:
        if phrase in text:
            intent.time_phrase = phrase
            intent.time_range = _resolve_time_phrase(phrase)
            break
    if intent.time_phrase is None:
        for w in VAGUE_TIME_WORDS:
            if w in text:
                intent.time_phrase = w  # deliberately left unresolved -> ambiguity
                break

    # 6. Filters -- captured as RAW candidate text only. Validity is decided
    #    later by the Ambiguity Detector against the live database, not here.
    city_match = CITY_FILTER_RE.search(text)
    if city_match:
        intent.filters["city"] = city_match.group(1).strip().title()

    category_match = CATEGORY_FILTER_RE.search(text)
    if category_match:
        intent.filters["category"] = category_match.group(1).strip().title()
    else:
        # Soft recognition: check the question against the ACTUAL category
        # values currently in the database (read once per parse), so a bare
        # "electronics products" is recognised without the word "category" --
        # this reads the database, it does not hardcode a category list.
        try:
            live_categories = _schema.distinct_values("products", "category")
        except Exception:
            live_categories = []
        for cat in live_categories:
            if cat.lower() in text:
                intent.filters["category"] = cat
                break

    if "cancelled" in text or "canceled" in text:
        intent.filters["status"] = "cancelled"
    elif "delivered" in text:
        intent.filters["status"] = "delivered"
    elif "shipped" in text:
        intent.filters["status"] = "shipped"
    elif "placed" in text:
        intent.filters["status"] = "placed"

    # 7. group by -------------------------------------------------------------
    if "by category" in text or "per category" in text or "each category" in text:
        intent.group_by = "category"
    elif "by customer" in text or "per customer" in text or "each customer" in text:
        intent.group_by = "customer"
    elif "by city" in text or "per city" in text or "each city" in text:
        intent.group_by = "city"

    # 8. limit -----------------------------------------------------------------
    limit_match = LIMIT_RE.search(text)
    if limit_match:
        intent.limit = int(limit_match.group(1))
    elif intent.superlative:
        intent.limit = 1  # "the best X" (singular) unless "top N" was given above
    elif intent.group_by or intent.aggregation == "COUNT":
        intent.limit = 100  # listing/aggregate queries: return all matching rows

    return intent
