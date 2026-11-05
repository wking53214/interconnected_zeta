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
