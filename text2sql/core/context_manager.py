"""
context_manager.py
--------------------
MODULE: Conversation / Context Manager

With the LLM-backed reasoning layer, "remembering what was asked" is
mostly just "showing the model the full conversation again" -- an LLM
call is stateless, so query_handler.py resends the whole transcript every
turn (see reasoner.py), and the model itself is what resolves "Revenue"
as an answer to its own previous clarifying question. That is a real,
legitimate implementation of Conversation/Context Manager; it just moves
where the resolution happens.

This module's remaining job is therefore two things every Reasoner
implementation needs regardless of *how* it reasons:
  1. Keep the per-session chat transcript (role + text), in the exact
     shape the LLM APIs expect ({"role": "user"|"assistant", "content": ...}),
     so query_handler.py can hand it straight to reasoner.reason().
  2. Keep a per-session query history for the UI's history sidebar.

The rule-based fallback (RuleBasedReasoner) additionally keeps its OWN
small pending-slot state internally (see reasoner.py / intent_resolution.py)
since it genuinely needs structured state, not just raw text -- that is
deliberately NOT stored here, to keep this module's contract the same
regardless of which reasoning strategy is active.
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class SessionState:
    conversation: List[dict] = field(default_factory=list)   # [{"role", "content"}, ...] chat turns
    query_history: List[dict] = field(default_factory=list)  # executed SQL + results log, for the UI


class ContextManager:
    def __init__(self):
        self._sessions: Dict[str, SessionState] = {}

    def _get_or_create(self, session_id: str) -> SessionState:
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState()
        return self._sessions[session_id]

    # -- transcript ---------------------------------------------------------
    def log_turn(self, session_id: str, role: str, content: str) -> None:
        state = self._get_or_create(session_id)
        state.conversation.append({"role": role, "content": content})

    def get_conversation(self, session_id: str) -> List[dict]:
        """Full transcript in LLM-message shape: [{"role": "user"|"assistant", "content": str}, ...]."""
        return self._get_or_create(session_id).conversation

    # -- query history (for the UI sidebar) ----------------------------------
    def log_query(self, session_id: str, entry: dict) -> None:
        state = self._get_or_create(session_id)
        state.query_history.append(entry)

    def get_query_history(self, session_id: str) -> List[dict]:
        return self._get_or_create(session_id).query_history

    # -- reset ----------------------------------------------------------------
    def reset(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
