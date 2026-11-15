"""A failed evaluation changes nothing, so a retry of the same observation counts once.

Before this change an exception between two locks of one evaluate_all (or after one lock's state was changed) left that state advanced.
Retrying the same observation then counted it twice, and one real observation could open a lock whose dwell threshold is 2.
"""
import threading
from datetime import datetime, timedelta

import pytest

from zeta import Key, KeySet, LockEvaluator, LockRegistry, LockSpec, LockStateStore

T0 = datetime(2026, 1, 1, 12, 0, 0)


def present():
    return KeySet([Key(name="a", present=True)])


def two_locks(dwell=2):
    return [LockSpec(lock_id="l1", required_keys=("a",), dwell_threshold=dwell),
            LockSpec(lock_id="l2", required_keys=("a",), dwell_threshold=dwell)]


def fail_once_on(evaluator, lock_id):
    real = evaluator._evaluate
    state = {"armed": True}

    def flaky(entity, keys, lid, ts):
        if lid == lock_id and state["armed"]:
            state["armed"] = False
            raise RuntimeError("interrupted")
        return real(entity, keys, lid, ts)

    evaluator._evaluate = flaky
    return state


def test_an_interrupted_pass_changes_no_lock_and_the_retry_counts_once():
    store = LockStateStore()
    evaluator = LockEvaluator(LockRegistry(two_locks()), store)
    fail_once_on(evaluator, "l2")
    with pytest.raises(RuntimeError):
        evaluator.evaluate_all("e", present(), T0)
    assert store.snapshot("e") == {}  # nothing advanced, nothing created
    out = evaluator.evaluate_all("e", present(), T0)  # the retry of the same single observation
    assert out["l1"].open is False and out["l2"].open is False  # dwell 2: one real observation does not open anything
    out = evaluator.evaluate_all("e", present(), T0)  # a second real observation
    assert out["l1"].open is True and out["l2"].open is True


def test_an_interrupted_pass_restores_a_lock_that_already_had_history():
    store = LockStateStore()
    evaluator = LockEvaluator(LockRegistry(two_locks(dwell=3)), store)
    evaluator.evaluate_all("e", present(), T0)  # one confirmation each
    before = store.snapshot("e")
    fail_once_on(evaluator, "l2")
    with pytest.raises(RuntimeError):
        evaluator.evaluate_all("e", present(), T0 + timedelta(seconds=1))
    assert store.snapshot("e") == before


def test_a_failed_single_evaluation_changes_nothing():
    store = LockStateStore()
    evaluator = LockEvaluator(LockRegistry(two_locks()), store)
    fail_once_on(evaluator, "l1")
    with pytest.raises(RuntimeError):
        evaluator.evaluate("e", present(), "l1", T0)
    assert store.snapshot("e") == {}


def test_a_failure_after_the_state_changed_is_also_undone():
    """evaluate fingerprints the KeySet after the state was updated; a KeySet that cannot be fingerprinted must not leave the lock advanced."""
    store = LockStateStore()
    evaluator = LockEvaluator(LockRegistry(two_locks()), store)

    class Unfingerprintable(KeySet):
        def fingerprint(self):
            raise RuntimeError("cannot fingerprint")

    with pytest.raises(RuntimeError):
        evaluator.evaluate("e", Unfingerprintable([Key(name="a", present=True)]), "l1", T0)
    assert store.snapshot("e") == {}


def test_a_failure_for_one_entity_leaves_every_other_entity_alone():
    store = LockStateStore()
    evaluator = LockEvaluator(LockRegistry(two_locks()), store)
    evaluator.evaluate_all("other", present(), T0)
    other_before = store.snapshot("other")
    fail_once_on(evaluator, "l2")
    with pytest.raises(RuntimeError):
        evaluator.evaluate_all("e", present(), T0)
    assert store.snapshot("other") == other_before


def test_a_stale_observation_for_the_second_lock_undoes_the_first():
    """The stale guard refuses a late observation; if it is refused for the second lock the first lock must not have counted it."""
    registry = LockRegistry(two_locks())
    store = LockStateStore()
    evaluator = LockEvaluator(registry, store)
    evaluator.evaluate("e", present(), "l2", T0 + timedelta(hours=1))  # l2 has seen a newer observation
    before = store.snapshot("e")
    out = evaluator.evaluate_all("e", present(), T0)  # l1 would count it; l2 refuses it as stale
    # the stale refusal is a normal result, not an exception: the pass completes and l1 counted the observation once
    assert store.snapshot("e")["l1"].pending_since_count == 1
    assert store.snapshot("e")["l2"] == before["l2"]


def test_a_successful_pass_is_unchanged():
    store = LockStateStore()
    evaluator = LockEvaluator(LockRegistry(two_locks()), store)
    first = evaluator.evaluate_all("e", present(), T0)
    second = evaluator.evaluate_all("e", present(), T0)
    assert (first["l1"].open, second["l1"].open, second["l1"].changed) == (False, True, True)


def test_threads_that_fail_do_not_corrupt_the_state_of_threads_that_succeed():
    store = LockStateStore()
    evaluator = LockEvaluator(LockRegistry(two_locks(dwell=40)), store)
    real = evaluator._evaluate
    guard = threading.Lock()
    counter = {"n": 0}

    def sometimes_fails(entity, keys, lid, ts):
        with guard:
            counter["n"] += 1
            fail = entity == "bad" and lid == "l2"
        if fail:
            raise RuntimeError("interrupted")
        return real(entity, keys, lid, ts)

    evaluator._evaluate = sometimes_fails

    def worker(i):
        for _ in range(40):
            if i % 2:
                with pytest.raises(RuntimeError):
                    evaluator.evaluate_all("bad", present(), T0)
            else:
                evaluator.evaluate_all("good", present(), T0)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads)
    assert store.snapshot("bad") == {}
    assert store.snapshot("good")["l1"].open is True  # 3 good threads x 40 = 120 confirmations, dwell 40: opened exactly once and stayed
