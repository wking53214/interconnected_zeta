"""LockState / LockStateStore: persistent, per-entity, per-lock state.

Generalizes two patterns that exist today as separate, narrower mechanisms:

  - OBSERVE's EscalationPolicy instance (observe_consolidated.py:846-889)
    holds exactly this shape of state (current_regime, pending_regime,
    dwell_count, escalation_locked, last_escalation_time) but as ONE shared
    instance per patient, wearing multiple unrelated hats: the same fields
    are reused for regime escalation AND (separately) for the data-integrity
    fault path (observe_consolidated.py:1345-1361). LockStateStore instead
    keeps one independent LockState per (entity, lock) pair, so N named
    locks per entity never share fields.

  - PERCEIVE's GovernanceState (perceive_consolidated.py:823-852) keys
    history by subject_id/rule_type and derives all decisions against a
    caller-supplied reference timestamp rather than wall-clock time, so
    identical inputs always replay to identical outputs. LockStateStore
    keeps that same determinism discipline: every transition in
    evaluator.py takes `timestamp` as an explicit argument.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Dict, Optional


@dataclass
class LockState:
    """Mutable state for one (entity, lock) pair.

    `pending_open` + `pending_since_count` mirror OBSERVE's
    `pending_regime` + `dwell_count` (observe_consolidated.py:853-854):
    dwell debounces a transition in EITHER direction (opening OR closing),
    matching the source's actual behavior — de-escalation requires the same
    dwell_threshold consecutive confirmations as escalation does, so a
    single normal reading right after a real spike doesn't immediately
    flip the lock back closed.
    """

    open: bool = False
    pending_open: Optional[bool] = None
    pending_since_count: int = 0
    locked_until: Optional[datetime] = None
    opened_at: Optional[datetime] = None
    forced: bool = False


class LockStateStore:
    """entity_id -> lock_id -> LockState, created on first access.

    In-memory only. A caller wanting durability persists snapshots via
    `snapshot`/`restore` themselves (e.g. to the immutable ledger in
    interconnected_delta) — this store's job is correct in-process state
    transitions, not storage.
    """

    def __init__(self) -> None:
        self._states: Dict[str, Dict[str, LockState]] = {}

    def get(self, entity_id: str, lock_id: str) -> LockState:
        entity_states = self._states.setdefault(entity_id, {})
        return entity_states.setdefault(lock_id, LockState())

    def set(self, entity_id: str, lock_id: str, state: LockState) -> None:
        self._states.setdefault(entity_id, {})[lock_id] = state

    def snapshot(self, entity_id: str) -> Dict[str, LockState]:
        # `replace(state)` (with no field changes) makes a real per-object
        # copy. A plain `dict(...)` only copies the outer mapping — the
        # LockState objects themselves would stay aliased to the live
        # store, so a later `evaluate()` call (which mutates LockState
        # in place) would silently corrupt an already-taken "snapshot".
        return {lock_id: replace(state) for lock_id, state in self._states.get(entity_id, {}).items()}

    def restore(self, entity_id: str, states: Dict[str, LockState]) -> None:
        self._states[entity_id] = {lock_id: replace(state) for lock_id, state in states.items()}
