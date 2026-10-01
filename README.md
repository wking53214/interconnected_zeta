# interconnected_zeta (ζ)

Domain-agnostic **Locks** layer. Version `0.1.0`. Stdlib only. Python ≥ 3.9.

## 1. Pipeline Position & Role

**INTERLOCKS.** Second stage of the extracted decision spine.

```text
α Alpha (Keys) → ζ Zeta (this repo) → β Beta (Decision) → δ Delta (custody)
```

Keys open Locks under declared `AND` / `OR` / `N_OF_M` combination, with per-lock dwell debounce, cooldown latch, and optional force-open. Live hub: [`observe-perceive`](https://github.com/wking53214/observe-perceive).

## 2. Full System Scope & Architectural Depth

ζ is an **extraction**, not a new invention. The same shape — registry of required conditions + evaluator + debounce/cooldown + force-vs-soft — was built independently at least five times in OBSERVE/PERCEIVE, never unified:

| Piece | Extracted from | Generalized |
|---|---|---|
| `LockSpec` registry | PERCEIVE `MODIFICATION_LEVELS` / `RISK_TIERS` / `EXPORT_RESTRICTIONS` / `OVERRIDE_CATEGORIES` | Named entry → conditions. Not 1:1 semantic coverage (see Gaps). |
| Dwell + cooldown | OBSERVE `EscalationPolicy.evaluate` | One shared regime per patient → N independent locks per entity. |
| Force bypass | `CLINICAL_SAFETY_BYPASS` | Inline OR-gate → declarable `force=True`. |
| Combination | PERCEIVE `ConsensusEngine` (AND-only) | AND / OR / N_OF_M. |
| Deterministic time | PERCEIVE `GovernanceState` | Every transition takes an explicit `timestamp`. Never wall-clock. |

### Mechanics

- **`Key`**: `name`, `present`, `confidence∈[0,1]`, `reason`. Frozen.
- **`KeySet`**: name-indexed; `min_confidence` filters what counts as present.
- **`LockSpec`**: `lock_id`, `required_keys` (non-empty, duplicate-free), `combination`, `n` (N_OF_M), `dwell_threshold≥1`, `lock_seconds≥0`, `force`.
- **`LockState`**: `open`, `pending_open`, `pending_since_count`, `locked_until`, `opened_at`, `forced`.
- **`LockStateStore`**: in-memory `entity_id → lock_id → LockState`. `snapshot`/`restore` copy via `dataclasses.replace` so snapshots are not aliased to live objects.
- **`LockEvaluator`**: `evaluate(entity_id, keys, lock_id, timestamp)` and `evaluate_all`. Unknown lock → `KeyError`. Unknown combination → `ValueError` (fail-closed).

Dwell is **symmetric**: opening and closing both require `dwell_threshold` consecutive confirming observations, matching OBSERVE (a single normal reading after a spike must not immediately flip closed). `force=True` skips dwell. After open, `lock_seconds` latches the lock open and ignores new input (cooldown). `lock_seconds=0` means no latch.

### Layout

```
zeta/keys.py        Key, KeySet
zeta/locks.py       Combination, LockSpec, LockRegistry
zeta/state.py       LockState, LockStateStore
zeta/evaluator.py   LockEvaluator, LockResult
```

## 3. What It Does NOT Do / Non-Goals

- Does **not** detect Keys (α) or emit a Decision narrative (β).
- Does **not** persist. In-memory only. Durability is the caller's job (δ / sentinel_os).
- Does **not** issue authorization. An open lock is a boolean interlock, not a grant.
- Does **not** implement pre-open refractory (`temporal_lock_hours` — minimum wait *before* opening). `lock_seconds` is post-open cooldown, the opposite polarity.
- Does **not** represent "always-open" (`required_keys` must be non-empty) or categorical kill-switches (`OVERRIDE_CATEGORIES['allowed']`).
- Does **not** reproduce `ConsensusEngine`'s continuous confidence score. `LockResult` is boolean + metadata.
- Does **not** talk to Postgres/Redis/KMS.
- **Not thread-safe.** `LockRegistry.register` is two dict operations.

## 4. Brutally Honest Current Status & Gaps

| Gap | Detail |
|---|---|
| Not on the live orchestrator path | observe-perceive still uses `EscalationPolicy` / PERCEIVE dicts. ζ is a composable extract. Dual implementation. |
| In-memory | Process crash loses all lock state. No snapshot-to-disk helper. |
| Uncalibrated tunables | `dwell_threshold`, `lock_seconds` are caller-chosen. No recommended production values. |
| Incomplete PERCEIVE coverage | Numeric elapsed-time thresholds, always-open aggregate export, unconditional reject categories: **not representable**. |
| No distributed consensus | Multi-region lock agreement does not exist. A second process has a different `LockStateStore`. |
| No Raft/Paxos | Documented as a future epic, not present. |
| Git consumers | α/β/δ depend on this via git URL. δ and β pin nothing more specific than the repo URL (unpinned default branch). |

Tests in `tests/`. Stdlib + pytest.

## 5. Core Invariants & Guarantees

- Fail-closed: unknown lock, empty `required_keys`, duplicate keys, illegal `n`, unknown combination → raise.
- Unevaluated locks are not "closed"; they are unknown. β treats unknown as non-matching.
- Determinism: same `(entity, keys, lock, timestamp, prior state)` → same `LockResult`.
- Snapshot isolation: `snapshot()` is a deep copy of `LockState` objects.
- Duplicate `required_keys` rejected so N_OF_M cannot be double-counted.

No guarantee of durability, cross-process agreement, or that an open lock means "allowed to act".

## 6. Inputs, Outputs & Type Contracts

```python
from datetime import datetime
from zeta import Combination, Key, KeySet, LockEvaluator, LockRegistry, LockSpec

registry = LockRegistry([
    LockSpec(
        lock_id="sepsis_lock",
        required_keys=("septic_shock", "respiratory_distress", "hypovolemic_shock"),
        combination=Combination.OR, force=True, lock_seconds=3600,
    ),
])
evaluator = LockEvaluator(registry)
keys = KeySet([Key(name="septic_shock", present=True, reason="...")])
result = evaluator.evaluate("patient_1", keys, "sepsis_lock", datetime.now())
# LockResult: lock_id, open, changed, forced, keys_satisfied, ...
```

`LockResult.changed` is what β uses as the nearest analogue of OBSERVE's `escalation_required`.

## 7. Stack Integration Topology

```text
α.observe(vitals) → KeySet
                       │
                       ▼
              ζ.LockEvaluator  (in-process LockStateStore)
                       │  Dict[lock_id, LockResult]
                       ▼
              β.DecisionEngine.decide
                       │
                       ▼
              δ.DecisionLedger.append   ← caller may also persist ζ.snapshot()
```

Downstream: [`interconnected_beta`](https://github.com/wking53214/interconnected_beta).  
Source of extraction: OBSERVE `observe_consolidated.py`, PERCEIVE `perceive_consolidated.py`.  
Custody of lock snapshots: not implemented here; intended consumer is δ / sentinel_os.

Proprietary. Copyright (c) 2026 William King. All rights reserved. See LICENSE.
