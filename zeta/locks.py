"""LockSpec / LockRegistry: declarative gate definitions.

Generalizes the registry SHAPE (a named dict entry mapping to conditions,
checked by a hand-written `can_X` method) already used independently, four
times, in the original private implementation:
  - the escalation policy's risk-tier table
  - the rule-modification policy's modification-level table
  - the data-export policy's export-restriction table
  - the emergency-override policy's override-category table

plus the dwell/lock_seconds/force parameters already used (uniquely,
per-instance, non-reusably) by the original private escalation policy's
constructor (dwell_threshold, lock_seconds) and its force-bypass path.

IMPORTANT: this generalizes the SHAPE, not full 1:1 semantic coverage of
all four source registries. An adversarial review against the original
private implementation found real gaps, kept here rather than glossed over:

  - The risk-tier table is dead configuration: the escalation check never
    reads it. There is no live `can_X` check for LockSpec to have
    generalized here, only the shape of the (unused) dict.
  - The override-category table's audit-required field is likewise declared
    but never read by the override check. Only 3 of its 4 fields are
    actually enforced in the original; LockSpec has no equivalent of the
    unused 4th.
  - The export-restriction table's aggregate-only entry requires zero
    conditions (always approved). LockSpec cannot represent this, because
    required_keys must be non-empty. An "always-open" lock is out of
    scope for this module as written.
  - The modification-level table's fields (an approval requirement and a
    temporal-lock duration in hours)
    are numeric/elapsed-time thresholds compared against counters, not
    boolean Key-present conditions, and the temporal-lock duration is a
    *minimum wait since the last event before the gate may open*, the
    opposite of `lock_seconds` (which starts a cooldown *after* opening).
    LockSpec has no field for a pre-open refractory period; this pattern
    is not yet covered.
  - The override-category table's "allowed" flag is a categorical kill-switch
    (unconditional rejection regardless of any input) that does not fit
    the required_keys + combination shape without fabricating a
    never-present Key as a workaround. Not currently supported directly.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Optional, Set, Tuple


class Combination(str, Enum):
    AND = "AND"
    OR = "OR"
    N_OF_M = "N_OF_M"


@dataclass(frozen=True)
class LockSpec:
    """Declarative definition of one gate.

    lock_id:         unique name (e.g. "sepsis_lock", "discharge_lock")
    required_keys:   Key names this lock evaluates
    combination:     AND (all required), OR (any one), N_OF_M (at least `n`)
    n:               required count when combination is N_OF_M
    dwell_threshold: consecutive satisfying observations needed before the
                     lock opens (1 = opens on first observation, no debounce).
                     Mirrors the original private dwell threshold, but
                     generalized: the original hardcodes this to exactly one
                     shared regime value; here
                     it is a per-lock parameter, so many named locks can each
                     have their own debounce sensitivity.
    lock_seconds:    once open, how long the lock stays latched open and
                     ignores new input (0 = no cooldown). Mirrors the original
                     private cooldown latch.
    force:           if True, a satisfying observation opens the lock
                     immediately, skipping dwell_threshold entirely. Mirrors
                     the original private force bypass, which
                     is exactly this behavior but hardcoded as one inline
                     OR over two rule-fired flags rather than a
                     reusable, declarable flag.
    """

    lock_id: str
    required_keys: Tuple[str, ...]
    combination: Combination = Combination.AND
    n: Optional[int] = None
    dwell_threshold: int = 1
    lock_seconds: float = 0.0
    force: bool = False

    def __post_init__(self) -> None:
        if not self.lock_id:
            raise ValueError("lock_id must be non-empty")
        if not self.required_keys:
            raise ValueError(f"Lock '{self.lock_id}': required_keys must be non-empty")
        if len(set(self.required_keys)) != len(self.required_keys):
            raise ValueError(
                f"Lock '{self.lock_id}': required_keys contains duplicates "
                f"{self.required_keys!r}; each key name must appear once "
                f"(a duplicate would double-count toward N_OF_M's n)"
            )
        if self.combination == Combination.N_OF_M:
            if not self.n or self.n < 1:
                raise ValueError(f"Lock '{self.lock_id}': N_OF_M combination requires n >= 1")
            if self.n > len(self.required_keys):
                raise ValueError(
                    f"Lock '{self.lock_id}': n={self.n} exceeds "
                    f"required_keys count ({len(self.required_keys)})"
                )
        if self.dwell_threshold < 1:
            raise ValueError(f"Lock '{self.lock_id}': dwell_threshold must be >= 1")
        if self.lock_seconds < 0:
            raise ValueError(f"Lock '{self.lock_id}': lock_seconds must be >= 0")

    def keys_satisfied(self, present_names: Set[str]) -> bool:
        # required_keys is guaranteed duplicate-free by __post_init__; using
        # set intersection (rather than summing over the tuple) keeps that
        # guarantee airtight even if this method is ever called on a
        # LockSpec built by some other path that skips validation.
        matched = len(set(self.required_keys) & present_names)
        if self.combination == Combination.AND:
            return matched == len(self.required_keys)
        if self.combination == Combination.OR:
            return matched >= 1
        if self.combination == Combination.N_OF_M:
            return matched >= (self.n or 0)
        raise ValueError(f"Lock '{self.lock_id}': unknown combination {self.combination!r}")


class LockRegistry:
    """Holds LockSpecs as data, replacing the pattern of one hardcoded dict
    per policy class (risk tiers / modification levels / export restrictions /
    override categories) with one reusable, domain-agnostic table."""

    def __init__(self, specs: Optional[Iterable[LockSpec]] = None) -> None:
        self._specs: Dict[str, LockSpec] = {}
        for s in specs or ():
            self.register(s)

    def register(self, spec: LockSpec) -> None:
        if spec.lock_id in self._specs:
            raise ValueError(f"Lock '{spec.lock_id}' already registered")
        self._specs[spec.lock_id] = spec

    def get(self, lock_id: str) -> LockSpec:
        try:
            return self._specs[lock_id]
        except KeyError:
            raise KeyError(f"Unknown lock '{lock_id}'") from None

    def all(self) -> List[LockSpec]:
        return list(self._specs.values())

    def __contains__(self, lock_id: str) -> bool:
        return lock_id in self._specs

    def __len__(self) -> int:
        return len(self._specs)
