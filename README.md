# interconnected_zeta

A domain-agnostic **Locks** layer.

A **Lock** is a barrier that requires specific detected **Keys**, in a
declared combination (`AND` / `OR` / `N_OF_M`), before it opens. Locks
persist state per entity, over time — dwell debounce in both directions,
a cooldown latch once open, and an optional force-bypass for conditions
that must not wait for confirmation.

## Why this exists

This is not a new invention. It is an **extraction**. The same shape —
registry of required conditions + evaluator + debounce/cooldown + a
force-vs-soft toggle — was built independently, at least five times,
across the existing governance stack, never unified:

| Piece of zeta | Extracted from | What it generalizes |
|---|---|---|
| `LockSpec` registry shape | `RuleModificationPolicy.MODIFICATION_LEVELS`, `EscalationPolicy.RISK_TIERS`, `DataExportPolicy.EXPORT_RESTRICTIONS`, `EmergencyOverridePolicy.OVERRIDE_CATEGORIES` (`perceive_consolidated.py:142-259`) | The SHAPE of four separate hardcoded dicts (a named entry mapping to conditions). See caveats below — this is not full 1:1 semantic coverage. |
| Dwell debounce + cooldown latch | `EscalationPolicy.evaluate` (`observe_consolidated.py:846-889`) | A real, correct hysteresis state machine — but hardcoded to debounce exactly one shared scalar (`current_regime`) per patient. Generalized here to any number of independently named locks per entity. |
| Force bypass | `CLINICAL_SAFETY_BYPASS` (`observe_consolidated.py:1280-1305`) | An inline `hard_rule_fired or syndrome_fired` OR-gate that skips dwell entirely, inseparable from the orchestration code around it. Generalized here to a declarable `force=True` flag on any lock. |
| AND/OR/N-of-M combination | `ConsensusEngine.evaluate` (`perceive_consolidated.py:483-504`) | The boolean approval gate — a fixed `all(...)`, AND-only. See caveat below: this omits ConsensusEngine's continuous confidence score, which `LockResult` does not reproduce. |
| Deterministic, reference-time-driven state | `GovernanceState` (`perceive_consolidated.py:823-852`) | Keys history by id and derives all decisions against a caller-supplied timestamp, never wall-clock time. `LockStateStore`/`LockEvaluator` follow the same discipline — every transition takes `timestamp` as an explicit argument. |

None of those five implementations has all four defining properties at
once (declarative registry, configurable combination, persistent
open/closed state, force-vs-soft toggle) — every one of them has 1-2.
`zeta` is the one place that has all four, so the other four call sites
can eventually import this instead of maintaining their own copy.

**One subtlety preserved faithfully:** OBSERVE's dwell debounce is
*symmetric*. De-escalating requires the same number of consecutive
confirming observations as escalating does — a single normal reading
right after a real spike does not immediately flip a lock back closed.
`zeta` reproduces this exactly (see `test_closing_requires_same_dwell_threshold_as_opening`
in `tests/test_evaluator.py`). Only the *force* bypass is asymmetric,
matching the source: it only ever forces the *opening* direction; closing
always goes through normal dwell confirmation, even for a `force=True` lock.

**Caveats found by adversarial review, not glossed over:**

- `RISK_TIERS` is **dead configuration** in the source — `can_escalate`
  never reads it. There was no live check for `LockSpec` to generalize
  here; only the shape of an unused dict.
- `OVERRIDE_CATEGORIES['audit_required']` is likewise declared but never
  read by `can_override` — only 3 of its 4 fields are actually enforced
  in PERCEIVE today.
- `EXPORT_RESTRICTIONS['aggregate_only']` requires zero conditions
  (always approved). `LockSpec` cannot represent this — `required_keys`
  must be non-empty — so "always-open" locks are out of scope as written.
- `MODIFICATION_LEVELS`' fields are numeric/elapsed-time thresholds
  (an approval count, and a *minimum wait since the last event* before
  re-arming), not boolean Key-present conditions. `temporal_lock_hours`
  is the opposite of `lock_seconds` (which cools down *after* opening,
  not *before*). This pattern isn't covered by `LockSpec` yet.
- `OVERRIDE_CATEGORIES['allowed']` is a categorical kill-switch
  (unconditional rejection regardless of any input), which doesn't fit
  the required-keys-plus-combination shape without a workaround.
- `ConsensusEngine.evaluate` also computes a geometric-mean confidence
  score across gate outputs, used downstream in the verdict and audit
  hash. `LockResult` has no equivalent confidence field — this module
  reproduces the open/closed gating decision, not that aggregation.

See `zeta/locks.py` and `zeta/evaluator.py` module docstrings for the
same caveats inline with the code they apply to.

## What zeta does NOT claim to be

The generic mechanism here (registry + AND/OR/N-of-M + dwell + cooldown +
force) is not novel in the abstract — rules engines and industrial
control-system interlocks have used this shape for decades. The
defensible value is not this engine; it's what gets configured into it
(the specific Keys, thresholds, and Lock combinations for a given domain,
validated against real outcomes over time). This module's job is to stop
that domain-specific configuration from being reimplemented, with subtly
inconsistent debounce/cooldown semantics, a sixth time.

## API

```python
from datetime import datetime
from zeta import Key, KeySet, LockSpec, LockRegistry, LockEvaluator, Combination

# 1. Declare locks (data, not code).
registry = LockRegistry([
    LockSpec(
        lock_id="sepsis_lock",
        required_keys=("fever", "tachycardia", "tachypnea"),
        combination=Combination.N_OF_M,
        n=2,                 # any 2 of the 3 -> lock opens
        dwell_threshold=1,    # no debounce: opens on first observation
        force=True,           # ...and, being force=True, also bypasses dwell on close-then-reopen
    ),
    LockSpec(
        lock_id="discharge_lock",
        required_keys=("fever_improving", "alert", "feeding_tolerating"),
        combination=Combination.AND,  # all three required
        dwell_threshold=2,             # needs 2 consecutive confirming observations
        lock_seconds=3600,             # once open, latched for 1 hour
    ),
])

evaluator = LockEvaluator(registry)

# 2. Detect Keys in the entity's current state (this part is domain-specific
#    pattern-matching, left to the caller / interconnected_alpha).
keys = KeySet([
    Key(name="fever_improving", present=True, confidence=0.94, reason="temp declined 103->99.5 over 24h"),
    Key(name="alert", present=True, confidence=0.98),
    Key(name="feeding_tolerating", present=True, confidence=0.9),
    Key(name="fever", present=False),
    Key(name="tachycardia", present=False),
])

# 3. Evaluate every lock for this entity at this reference time.
results = evaluator.evaluate_all("patient_123", keys, datetime.now())

for lock_id, result in results.items():
    print(lock_id, result.open, result.reasons)
```

## Design notes

- **Stateless evaluator, stateful store.** `LockEvaluator` holds no
  per-entity data itself; all of it lives in `LockStateStore`, so one
  evaluator instance serves every entity and every lock in a domain.
- **Deterministic.** Every state transition takes an explicit `timestamp`
  argument. Nothing here calls the system clock. Identical input
  sequences replay to identical results (see
  `test_identical_input_sequence_yields_identical_results`).
- **Zero external dependencies.** Only the Python standard library.
  Everything that needs infrastructure (persistence, the immutable
  ledger) lives downstream in `interconnected_delta`.
- **`LockResult` matches the existing `(bool, List[str])` convention**
  already used by `can_escalate`/`can_modify`/`can_export`/`can_override`
  in PERCEIVE, so adopting `zeta` at a call site is a drop-in swap, not a
  calling-convention rewrite.

## Where this fits in the 4-repo architecture

```
interconnected_alpha  -- detects Keys in raw entity data
interconnected_zeta   -- (this repo) Keys -> Locks -> open/closed decisions
interconnected_beta   -- Lock states -> governance rules -> a decision + narrative
interconnected_delta  -- records the decision, tracks execution, verifies outcome
```

## Tests

```
pip install -e ".[dev]"
pytest
```

52 tests, zero external dependencies beyond `pytest` itself.
