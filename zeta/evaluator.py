"""LockEvaluator: the generic gating engine.

Replaces the hardcoded, single-purpose combination/debounce logic
duplicated across the governance stack:

  - The original private consensus step is a fixed all-approved check
    over the policy outputs, AND-only, no
    dwell, no cooldown, no force path, evaluated fresh every call with no
    persisted state. NOTE: it also computes a geometric-mean confidence
    score across all gate outputs, which is load-bearing downstream (the
    returned verdict, the audit hash, and optional multi-node input).
    LockResult has no equivalent confidence field; this evaluator
    reproduces the open/closed gating decision, not that continuous
    confidence aggregation, which remains a gap if a caller needs it.

  - The original private per-policy check methods (escalate, modify,
    export, override) each hand-write their own
    if-checks against a registry entry, returning (bool, List[str]),
    real, working gating, but re-implemented four separate times with no
    shared combination or state logic.

  - The original private escalation policy's evaluate step
    has real, correct dwell debounce and lock_seconds cooldown latching,
    and, importantly, that dwell debounce is SYMMETRIC: a transition
    away from the current regime (escalating OR de-escalating) both need
    `dwell_threshold` consecutive matching observations before they're
    confirmed. Only the resulting cooldown latch is
    asymmetric: it's only armed when the confirmed transition is an
    escalation. This evaluator reproduces both properties, generalized
    from one hardcoded scalar regime to a named Key combination per lock.
    One thing NOT reproduced: the original regime is 4-valued (stable/
    caution/warning/critical), so a dwell-confirmed transition it would
    treat as real (e.g. stable -> caution, no escalation) collapses here
    to "no change" if both map to the same lock's closed state. A single
    boolean Lock has no second axis to record "still closed, but which
    kind of closed changed." Model each such partition boundary as its
    own named lock if that granularity matters to a caller.

  - The original private force-bypass OR-gate
    is a real force-bypass for the OPENING direction only, inline
    as an OR over two rule-fired flags, inseparable from the escalation
    orchestration code around it, and with no equivalent bypass for
    closing (de-escalation always goes through the normal dwell path).

LockEvaluator is stateless; all persistent state lives in the supplied
LockStateStore, so one evaluator instance serves every entity and every
lock in a domain's LockRegistry.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List

from .keys import KeySet
from .locks import LockRegistry
from .state import LockStateStore


@dataclass(frozen=True)
class LockResult:
    """Outcome of evaluating one lock at one reference time.

    Shaped like the (bool, List[str]) "approved, violations" convention
    the original private per-policy check methods already use,
    so a LockResult drops directly into that calling convention.
    """

    lock_id: str
    open: bool
    changed: bool
    forced: bool
    reasons: List[str] = field(default_factory=list)


class LockEvaluator:
    def __init__(self, registry: LockRegistry, state_store: LockStateStore = None) -> None:
        self.registry = registry
        self.state_store = state_store if state_store is not None else LockStateStore()

    def evaluate(self, entity_id: str, keys: KeySet, lock_id: str, timestamp: datetime) -> LockResult:
        spec = self.registry.get(lock_id)
        state = self.state_store.get(entity_id, lock_id)

        # Cooldown latch: while locked, ignore new input entirely, in
        # either direction. Mirrors the original private cooldown check,
        # which returns early while less than lock_seconds has elapsed.
        if state.locked_until is not None:
            if timestamp < state.locked_until:
                return LockResult(
                    lock_id=lock_id,
                    open=state.open,
                    changed=False,
                    forced=state.forced,
                    reasons=[f"In cooldown until {state.locked_until.isoformat()}"],
                )
            state.locked_until = None

        present = keys.names_present()
        satisfied = spec.keys_satisfied(present)

        # Force bypass: only ever forces the OPENING direction, immediately,
        # skipping dwell. Mirrors the original private force bypass, which never
        # forces de-escalation. Only takes effect when the lock isn't already open.
        if satisfied and spec.force and not state.open:
            state.open = True
            state.pending_open = None
            state.pending_since_count = 0
            state.forced = True
            state.opened_at = timestamp
            if spec.lock_seconds > 0:
                state.locked_until = timestamp + timedelta(seconds=spec.lock_seconds)
            self.state_store.set(entity_id, lock_id, state)
            return LockResult(
                lock_id=lock_id,
                open=True,
                changed=True,
                forced=True,
                reasons=[f"Forced open: keys satisfied and lock '{lock_id}' is force=True"],
            )

        if satisfied == state.open:
            # Observation matches the currently-confirmed state: no
            # transition pending. Mirrors the original private implementation
            # resetting its pending regime and dwell count when the new regime
            # equals the current regime.
            state.pending_open = None
            state.pending_since_count = 0
            self.state_store.set(entity_id, lock_id, state)
            return LockResult(
                lock_id=lock_id,
                open=state.open,
                changed=False,
                forced=state.forced,
                reasons=["Already " + ("open" if state.open else "closed") + "; keys unchanged"],
            )

        # Observation differs from the confirmed state: accumulate dwell.
        # Mirrors the original private pending-regime and dwell-count
        # accumulation, generalized to a boolean
        # "pending open/closed" per (entity, lock) instead of one shared
        # counter per patient.
        if satisfied == state.pending_open:
            state.pending_since_count += 1
        else:
            state.pending_open = satisfied
            state.pending_since_count = 1

        if state.pending_since_count >= spec.dwell_threshold:
            was_open = state.open
            state.open = satisfied
            state.pending_open = None
            state.pending_since_count = 0
            if satisfied and not was_open:
                state.forced = False
                state.opened_at = timestamp
                if spec.lock_seconds > 0:
                    state.locked_until = timestamp + timedelta(seconds=spec.lock_seconds)
            elif not satisfied:
                state.forced = False
            self.state_store.set(entity_id, lock_id, state)
            direction = "opened" if satisfied else "closed"
            return LockResult(
                lock_id=lock_id,
                open=state.open,
                changed=True,
                forced=False,
                reasons=[f"Dwell threshold reached ({spec.dwell_threshold}); lock {direction}"],
            )

        self.state_store.set(entity_id, lock_id, state)
        return LockResult(
            lock_id=lock_id,
            open=state.open,
            changed=False,
            forced=state.forced,
            reasons=[
                f"Transition pending but dwell not yet confirmed "
                f"({state.pending_since_count}/{spec.dwell_threshold})"
            ],
        )

    def evaluate_all(self, entity_id: str, keys: KeySet, timestamp: datetime) -> Dict[str, LockResult]:
        return {
            spec.lock_id: self.evaluate(entity_id, keys, spec.lock_id, timestamp)
            for spec in self.registry.all()
        }
