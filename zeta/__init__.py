"""zeta: a domain-agnostic Locks layer.

A Lock is a barrier that requires specific detected Keys, in a declared
combination (AND / OR / N-of-M), before it opens. Locks persist state
(open/closed, dwell progress, cooldown) per entity, over time, and can be
declared to force-open (bypassing debounce) or to run advisory-only.

This module is an extraction, not a fresh invention. Every mechanic here
already existed, independently, at least five times across the governance
stack (PERCEIVE's RuleModificationPolicy/EscalationPolicy/DataExportPolicy/
EmergencyOverridePolicy, PERCEIVE's ConsensusEngine, PERCEIVE's
GovernanceState + PolicyEnforcementConfig, OBSERVE's EscalationPolicy dwell/
hysteresis state machine, and OBSERVE's CLINICAL_SAFETY_BYPASS OR-gate). See
README.md for the file/line provenance of each piece. zeta collapses those
five hardcoded, single-purpose implementations into one declarative,
reusable layer.
"""

from .keys import Key, KeySet
from .locks import Combination, LockSpec, LockRegistry
from .state import LockState, LockStateStore
from .evaluator import LockResult, LockEvaluator

__all__ = [
    "Key",
    "KeySet",
    "Combination",
    "LockSpec",
    "LockRegistry",
    "LockState",
    "LockStateStore",
    "LockResult",
    "LockEvaluator",
]

__version__ = "0.1.0"
