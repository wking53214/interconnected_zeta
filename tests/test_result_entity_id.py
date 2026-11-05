"""LockResult.entity_id: the entity a result was evaluated for, set by LockEvaluator on every path.

It is a plain field, like keyset_fingerprint and stale: a result built by hand can carry any value.
"""
from datetime import datetime, timedelta

from zeta import Combination, Key, KeySet, LockEvaluator, LockRegistry, LockResult, LockSpec

T0 = datetime(2026, 1, 1, 12, 0, 0)


def sick():
    return KeySet([Key(name="septic_shock", present=True, confidence=1.0, reason="HR 148")])


def registry():
    return LockRegistry([
        LockSpec(lock_id="sepsis_lock", required_keys=("septic_shock",), combination=Combination.OR, lock_seconds=3600),
        LockSpec(lock_id="slow_lock", required_keys=("septic_shock",), dwell_threshold=3),
    ])


def test_evaluate_records_the_entity_id_it_was_called_with():
    assert LockEvaluator(registry()).evaluate("p1", sick(), "sepsis_lock", T0).entity_id == "p1"


def test_evaluate_all_records_the_entity_id_on_every_result():
    results = LockEvaluator(registry()).evaluate_all("p7", sick(), T0)
    assert set(results) == {"sepsis_lock", "slow_lock"}
    assert {r.entity_id for r in results.values()} == {"p7"}


def test_entity_id_is_recorded_on_the_stale_cooldown_and_forced_paths_too():
    reg = LockRegistry([LockSpec(lock_id="l", required_keys=("septic_shock",), combination=Combination.OR,
                                 lock_seconds=3600, force=True)])
    ev = LockEvaluator(reg)
    first = ev.evaluate("p1", sick(), "l", T0)
    cooling = ev.evaluate("p1", sick(), "l", T0 + timedelta(seconds=10))
    stale = ev.evaluate("p1", sick(), "l", T0 - timedelta(hours=1))
    assert first.forced and stale.stale
    assert {first.entity_id, cooling.entity_id, stale.entity_id} == {"p1"}


def test_legacy_construction_without_entity_id_still_works():
    r = LockResult(lock_id="l", open=True, changed=False, forced=False)
    assert r.entity_id == ""
    assert LockResult("l", True, False, False, ["why"], True, "f" * 64).entity_id == ""


def test_entity_id_does_not_change_the_keyset_fingerprint():
    ks = sick()
    before = ks.fingerprint()
    r = LockEvaluator(registry()).evaluate("p1", ks, "sepsis_lock", T0)
    assert r.keyset_fingerprint == before == ks.fingerprint()


def test_fingerprint_stale_and_entity_id_are_plain_fields_any_builder_can_set():
    forged = LockResult(lock_id="sepsis_lock", open=True, changed=True, forced=False,
                        stale=True, keyset_fingerprint="a" * 64, entity_id="someone-else")
    assert (forged.stale, forged.keyset_fingerprint, forged.entity_id) == (True, "a" * 64, "someone-else")


# --- the connector refuses a result recorded for another entity (attack X1) ----------------------------------

import pytest  # noqa: E402

from zeta.cns_connector import lock_result_content  # noqa: E402

CTX = {"schema": "spine-context/1", "entity_id": "patient_1", "event_id": "event_1", "run_id": "run_1"}


def _bound(entity, ctx=CTX):
    keys = sick()
    result = LockEvaluator(registry()).evaluate(entity, keys, "sepsis_lock", T0)
    return lock_result_content(result, context=ctx, keyset_digest="a" * 64)


def test_a_result_evaluated_for_another_entity_is_not_bound_under_this_context():
    """X1: a result for patient_2 used to be bound under patient_1's context with entity_id taken from the context."""
    with pytest.raises(ValueError, match=r"patient_2.*patient_1"):
        _bound("patient_2")


def test_a_result_for_the_contexts_entity_is_bound():
    assert _bound("patient_1")["entity_id"] == "patient_1"


def test_a_legacy_result_with_an_empty_entity_id_is_still_bound():
    keys = sick()
    result = LockEvaluator(registry()).evaluate("patient_1", keys, "sepsis_lock", T0)
    legacy = LockResult(lock_id=result.lock_id, open=result.open, changed=result.changed, forced=result.forced,
                        reasons=list(result.reasons), stale=result.stale, keyset_fingerprint=result.keyset_fingerprint)
    assert legacy.entity_id == ""
    assert lock_result_content(legacy, context=CTX, keyset_digest="a" * 64)["entity_id"] == "patient_1"
