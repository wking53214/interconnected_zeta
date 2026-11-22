# interconnected_zeta (ζ)

Domain-agnostic **Locks** layer. Version `0.1.0`. Stdlib only. Python ≥ 3.10.

## Role Within ≡TACK

- **What it is.** The lock stage of the small reference spine: it turns named Keys into open or closed interlocks, with dwell, cooldown and force rules.
- **Authority matrix.** Evaluation only. An open lock is a boolean interlock, not authorization. It does not define policy, enforce, authenticate, or keep evidence.
- **Owns.** Lock specs and state, the evaluator, and the CNS binding of a `LockResult` and a snapshot.
- **Does not own.** Detecting Keys (alpha), decisions (beta), custody (delta), durability, or distributed agreement.
- **Depends on.** The standard library. CNS is optional.
- **Depended on by.** alpha, beta and delta each pin it by git commit (stated in section 4; not re-checked here).
- **Fail behavior.** Closed on bad config: unknown lock, empty or duplicate `required_keys`, bad `n` or unknown combination raise. A stale observation changes no state. State is in memory only, so a crash loses all lock state, and `evaluate` accepts any KeySet under any entity id (identity is the caller's). A lock that is already open reports no change when things get worse: when a second required Key appears on an open lock the result is `changed=False`, so a caller that reads `changed` as its escalation signal sees nothing new (EXECUTED 2026-11-17). Give each severity its own lock. Without `force=True`, a lock with `dwell_threshold` above 1 does not open on a first reading, and readings that alternate between present and absent never open it (EXECUTED 2026-11-17).

Status: library, version 0.1.0, Apache-2.0. EXECUTED 2026-11-17 at `97f7899`, Python 3.13, in a fresh environment: `pip install -e ".[dev]" && pytest` gives 129 passed, 2 skipped (the two need CNS). With the `cns` extra installed and `ZETA_REQUIRE_CNS=1`, which is how CI's second job runs, 237 passed and none skipped. Not run: GitHub CI, Python 3.10 to 3.12.

## 1. Pipeline Position & Role

**INTERLOCKS.** Second stage of the extracted decision spine.

```text
α Alpha (Keys) → ζ Zeta (this repo) → β Beta (Decision) → δ Delta (custody)
```

The order above is the design order. CNS's script `chains/spine_run.py` runs all four repos in this order (EXECUTED 2026-11-17: exit 0 against the four mains). observe-perceive's orchestrator does not call them.

Keys open Locks under declared `AND` / `OR` / `N_OF_M` combination, with per-lock dwell debounce, cooldown latch, and optional force-open. Orchestrator: [`observe-perceive`](https://github.com/wking53214/observe-perceive).

## 2. Full System Scope & Architectural Depth

ζ is an **extraction**, not a new invention. The same shape, registry of required conditions + evaluator + debounce/cooldown + force-vs-soft, was built independently at least five times in OBSERVE/PERCEIVE, never unified:

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

Stale observations are refused. Each lock remembers the newest observation time it has applied (`LockState.last_observed_at`). An observation older than that returns a `LockResult` with `stale=True` and a reason, and changes no state, in either direction (it can neither count toward opening nor toward closing). An observation at the same time as the newest is not stale. Mixing timezone-aware and naive timestamps raises `ValueError`. The CNS-bound snapshot content does not include `last_observed_at`; a bound `LockResult` carries `stale: true` only when it is stale, so existing digests do not change.

### Layout

```
zeta/keys.py        Key, KeySet
zeta/locks.py       Combination, LockSpec, LockRegistry
zeta/state.py       LockState, LockStateStore
zeta/evaluator.py   LockEvaluator, LockResult
zeta/signing.py     the lock-result signing slot (no key held here)
```

## 3. What It Does NOT Do / Non-Goals

- Does **not** detect Keys (α) or emit a Decision narrative (β).
- Does **not** persist. In-memory only. Durability is the caller's job (δ / sentinel_os).
- Does **not** issue authorization. An open lock is a boolean interlock, not a grant.
- Does **not** implement pre-open refractory (`temporal_lock_hours`, minimum wait *before* opening). `lock_seconds` is post-open cooldown, the opposite polarity.
- Does **not** represent "always-open" (`required_keys` must be non-empty) or categorical kill-switches (`OVERRIDE_CATEGORIES['allowed']`).
- Does **not** reproduce `ConsensusEngine`'s continuous confidence score. `LockResult` is boolean + metadata.
- Does **not** talk to Postgres/Redis/KMS.
- Does **not** hold, make, store or rotate a signing key. It calls a signer the operator plugs in (see **Signed lock results** below).
- **Thread safety is per object, and only in this process.** One `LockRegistry`, one `LockStateStore` and the evaluators that share them may be used from several threads: registering is one step, and an evaluation (the read, the change and the write of a lock's state) holds the store's reentrant `lock`, so no two overlap. Nothing here is safe across processes (the state is in memory), and a caller who reads a state and then acts on it in several steps must hold `store.lock` across them. Evaluation is also all or nothing: if `evaluate` or `evaluate_all` raises for any reason, the entity's lock states are restored to what they were before the call, so retrying the same observation counts it once (before this, an interrupted `evaluate_all` left earlier locks advanced and a retry could open a lock with one real observation).

## 4. Brutally Honest Current Status & Gaps

| Gap | Detail |
|---|---|
| Not on the orchestrator path | observe-perceive still uses `EscalationPolicy` / PERCEIVE dicts. ζ is a composable extract. Dual implementation. |
| In-memory | Process crash loses all lock state. No snapshot-to-disk helper. |
| Uncalibrated tunables | `dwell_threshold`, `lock_seconds` are caller-chosen. No recommended production values. |
| Incomplete PERCEIVE coverage | Numeric elapsed-time thresholds, always-open aggregate export, unconditional reject categories: **not representable**. |
| No distributed consensus | Multi-region lock agreement does not exist. A second process has a different `LockStateStore`. |
| No Raft/Paxos | Documented as a future epic, not present. |
| Git consumers | α, β and δ depend on this via git URL, each pinned by commit in its own `pyproject.toml` (and each repo has a test that holds the pin). Moving a pin is a reviewed change. |
| Identity is the caller's | `KeySet` carries no entity, event or run, a `LockResult` carries the `entity_id` it was evaluated for (never an event or run), and `evaluate` accepts any KeySet under any `entity_id`: the subject, event and run are whatever the binder's context says. What zeta does record, natively, is **which KeySet a result was evaluated from**: `KeySet.fingerprint()` (SHA-256 over `min_confidence` and every Key's name, presence, confidence and reason, so two sets of readings that list the same present Keys differ) is set on every `LockResult` as `keyset_fingerprint` by `evaluate`, on every path. A fingerprint is a fact computed here, not a signature: it holds no key, so someone who controls the evaluation can still lie, and a result built by hand records none. `LockResult.keyset_fingerprint`, `stale` and `entity_id` are plain dataclass fields: any caller who builds a `LockResult` can set them to any value, so they are not evidence of who produced a result. `LockEvaluator.evaluate` (and `evaluate_all`, which calls it) sets `entity_id` to the id it was called with; a hand-built result leaves it `""`. Zeta therefore proves content identity of what it evaluated (this KeySet fingerprint, this entity_id, these reasons), not who produced a result, unless the operator plugs in a signer (next row). |
| Signatures are opt-in and in-process | With a signer plugged in, every `LockResult` from `evaluate` carries a signature over all its other fields. With none (the default) results are unsigned exactly as before, and that setup is **unprotected**: anyone can build a `LockResult`. Even when signed, the signer runs in the same process as the evaluator, so any code there that can call it can sign a result it made up. A separate signer process is the next step. A `LockResult` has no time field, so a signature does not prove freshness; β binds time through the Decision and the prior lock-state digest. |

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
        lock_id="hazard_lock",
        required_keys=("signal_a", "signal_b", "signal_c"),
        combination=Combination.OR, force=True, lock_seconds=3600,
    ),
])
evaluator = LockEvaluator(registry)
keys = KeySet([Key(name="signal_a", present=True, reason="...")])
result = evaluator.evaluate("entity_1", keys, "hazard_lock", datetime.now())
# LockResult: lock_id, open, changed, forced, reasons, stale, keyset_fingerprint, entity_id, signature
```

`LockResult.changed` is what β uses as the nearest analogue of OBSERVE's `escalation_required`.

## Signed lock results

A lock result can be signed, so a later reader (β, δ, CNS) can check that the holder of the operator's key produced
it. zeta holds no key: it builds the exact bytes to sign and calls the `sign` callable the operator supplies (an
Ed25519 private key's `sign`, a KMS wrapper). Checking is the same: `verify_lock_result_signature(result,
result.signature, verify)` with the operator's `verify` callable.

```python
evaluator = LockEvaluator(registry, signer=private_key.sign)   # signer=None (default): nothing signed, nothing changes
result = evaluator.evaluate("entity_1", keys, "hazard_lock", now)
zeta.verify_lock_result_signature(result, result.signature, verify)   # True only for an untouched, signed result
```

What is signed: a domain label `zeta-lock-result-signature/1` and a newline, then `schema` (`zeta-lock-result/1`)
and every `LockResult` field except `signature` as canonical ASCII JSON (sorted keys, no spaces, no NaN), with every
string checked as strict UTF-8 first. These are interconnected_delta's receipt-signing rules, copied byte for byte;
`tests/test_signing.py` pins them, including DELTA's `strict_utf8` source. Only an exact `LockResult` with exact
field types and a non-empty `lock_id`, `entity_id` and `keyset_fingerprint` can be signed, so a hand-built result that
names no subject cannot be signed and replayed for any entity.

Fail closed: a signer that raises or returns anything but non-empty `bytes` raises out of `evaluate` /
`evaluate_all`, and the entity's lock states are put back, so no unsigned result escapes and a retry counts once.

Limits, plainly:

- **In-process forgery.** The signer is code in the same program. Anything that can call it can sign a lock result
  that no evaluation produced. A signature proves who held the signer, not that the evaluation was honest. The next
  step is a separate signer process that evaluates (or re-checks) before it signs.
- **No signer, no protection.** With `signer=None` results are unsigned and anyone can build one. β refuses unsigned
  or badly signed results only when a checker is plugged into it.
- **No freshness.** `LockResult` carries no time, so an old signed result for the same entity and KeySet verifies
  again. β's Decision (timestamp, `lock_results_digest`, `prior_state_digest`) and δ's ledger carry the time.
- **No key management.** Trust, rotation and revocation belong to the operator's callables.

## 7. Stack Integration Topology

```text
α.observe(reading) → KeySet
                       │
                       ▼
              ζ.LockEvaluator  (in-process LockStateStore, optional signer)
                       │  Dict[lock_id, LockResult]  (signed when a signer is plugged in)
                       ▼
              β.DecisionEngine.decide
                       │
                       ▼
              δ.DecisionLedger.append   ← caller may also persist ζ.snapshot()
```

Who runs this order: CNS's script `chains/spine_run.py`, not observe-perceive's orchestrator (see section 1 for the evidence).

Downstream: [`interconnected_beta`](https://github.com/wking53214/interconnected_beta).  
Source of extraction: OBSERVE `observe_consolidated.py`, PERCEIVE `perceive_consolidated.py`.  
Custody of lock snapshots: not implemented here; intended consumer is δ / sentinel_os.

## Optional CNS connection

zeta does not need CNS. It has no runtime dependency, nothing in the package imports CNS when it loads, and the evaluator never imports it (`tests/test_cns_independence.py` checks this with CNS blocked). `zeta/cns_connector.py` is the one place that knows CNS exists. Install it with `pip install 'zeta[cns] @ git+https://github.com/wking53214/interconnected_zeta'` (CNS needs Python 3.10+); without CNS, computing a digest raises `CnsNotInstalled` and nothing else changes.

It makes zeta's outputs **bindable** to a verdict in CNS's shared gate contract (`cns.gate`), so a verdict about an output can be checked against that output:

| Output | Bound content | Default subject |
|---|---|---|
| `LockResult` | `lock_id`, `open`, `changed`, `forced`, `reasons` (in the order written) | `zeta.lock_result` |
| `LockStateStore.snapshot(entity_id)` | every field of every `LockState`, keyed by `lock_id` (listing order cannot change the digest); times as `isoformat()` | `zeta.lock_snapshot` |

`lock_result_digest`, `snapshot_digest` and `binds(verdict, output)` are the whole surface; `lock_result_content` and `snapshot_content` return the plain content (no CNS needed). `binds` is true only for a verdict that names exactly that output (the subject and its digest), and false for an unbound one or one that names another output. It compares what a verdict names and does not check who made it: a `GateResult` typed by hand with the right subject and digest binds too (EXECUTED 2026-11-17), so `binds` alone is not evidence that any gate judged the output. Under a `context=`, a `LockResult` whose non-empty `entity_id` differs from the context's `entity_id` raises `ValueError` naming both; a legacy result with an empty `entity_id` is still accepted and takes the context's entity. Without a context, pass `entity_id=` to bind the entity.

**Binding a result to a run.** Pass `context=` (exactly `schema` `"spine-context/1"`, `entity_id`, `event_id`, `run_id`, all non-empty strings) and, for a `LockResult`, `keyset_digest=` (the digest of the exact KeySet it was evaluated from; both or neither). The content gains `context` and `keyset_digest`, so "this result came from that KeySet in that run" can be checked. An `entity_id=` that disagrees with the context raises. A snapshot takes a context only. Without them every existing digest is unchanged. Under a context the content also carries `keyset_fingerprint`, the one the result recorded when it was evaluated, and a result that recorded none (built by hand, or from an older zeta) is refused, so a context-bound result always says which KeySet it came from; alpha reports the same fingerprint for the KeySet it produced, which is how CNS matches the two. `keyset_digest` is still supplied by the binder (zeta cannot compute alpha's digest), and the fingerprint is what checks it.

This is **binding only**, on purpose. An open lock is a boolean interlock, not an authorization (see non-goals), so zeta offers no gate, no PASS / RETRY / BREACH mapping and no chain. Whoever judges a lock owns the verdict; zeta only lets that verdict be tied to the result it was about.

Apache-2.0.
