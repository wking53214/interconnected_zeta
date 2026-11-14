"""One registry, one state store and one evaluator may be used from several threads.

An evaluation reads a lock's state, changes it and writes it back. Two threads doing that for the same (entity, lock) at once
could count one confirmation twice or lose one. These tests use real threads and make the overlap certain instead of waiting
for bad luck: a step is slowed so that, without a lock, other threads are always inside it at the same moment.
"""
import threading
import time
from datetime import datetime

import pytest

from zeta import Combination, Key, KeySet, LockEvaluator, LockRegistry, LockSpec, LockStateStore

T0 = datetime(2026, 1, 1, 12, 0, 0)


def present(name="a"):
    return KeySet([Key(name=name, present=True)])


def run_threads(count, target):
    errors = []
    ready = threading.Barrier(count)

    def wrapped(index):
        try:
            ready.wait()
            target(index)
        except BaseException as exc:  # noqa: BLE001 - the test reports what a thread raised
            errors.append(exc)

    threads = [threading.Thread(target=wrapped, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a thread did not finish"
    return errors


def test_two_evaluations_of_the_same_lock_never_overlap():
    spec = LockSpec(lock_id="l1", required_keys=("a",), dwell_threshold=3)
    evaluator = LockEvaluator(LockRegistry([spec]))
    inside = [0]
    most = [0]
    guard = threading.Lock()
    real = evaluator._evaluate

    def slowed(*args, **kwargs):
        with guard:
            inside[0] += 1
            most[0] = max(most[0], inside[0])
        time.sleep(0.01)
        try:
            return real(*args, **kwargs)
        finally:
            with guard:
                inside[0] -= 1

    evaluator._evaluate = slowed
    assert run_threads(8, lambda i: evaluator.evaluate("e1", present(), "l1", T0)) == []
    assert most[0] == 1


def test_every_confirmation_is_counted_exactly_once():
    """Thirty confirmations from thirty threads open a lock whose dwell is thirty: no confirmation lost, none counted twice."""
    n = 30
    spec = LockSpec(lock_id="l1", required_keys=("a",), dwell_threshold=n)
    store = LockStateStore()
    evaluator = LockEvaluator(LockRegistry([spec]), store)
    results = []
    guard = threading.Lock()

    def one(i):
        r = evaluator.evaluate("e1", present(), "l1", T0)
        with guard:
            results.append(r)

    assert run_threads(n, one) == []
    assert len(results) == n
    assert sum(1 for r in results if r.changed) == 1  # it opened once, on the last confirmation
    state = store.get("e1", "l1")
    assert state.open is True and state.pending_since_count == 0


def test_evaluators_sharing_one_store_share_its_lock():
    spec = LockSpec(lock_id="l1", required_keys=("a",), dwell_threshold=20)
    registry = LockRegistry([spec])
    store = LockStateStore()
    evaluators = [LockEvaluator(registry, store), LockEvaluator(registry, store)]
    assert run_threads(20, lambda i: evaluators[i % 2].evaluate("e1", present(), "l1", T0)) == []
    assert store.get("e1", "l1").open is True


def test_evaluate_all_is_one_consistent_pass():
    specs = [LockSpec(lock_id=f"l{i}", required_keys=("a",), dwell_threshold=1) for i in range(5)]
    evaluator = LockEvaluator(LockRegistry(specs))
    seen = []
    guard = threading.Lock()

    def one(i):
        out = evaluator.evaluate_all("e1", present(), T0)
        with guard:
            seen.append(sorted(out))

    assert run_threads(6, one) == []
    assert all(names == [f"l{i}" for i in range(5)] for names in seen)


def test_a_snapshot_taken_during_evaluations_is_never_torn():
    spec = LockSpec(lock_id="l1", required_keys=("a",), dwell_threshold=2)
    store = LockStateStore()
    evaluator = LockEvaluator(LockRegistry([spec]), store)
    snaps = []
    guard = threading.Lock()

    def one(i):
        if i % 2:
            with guard:
                snaps.append(store.snapshot("e1"))
        else:
            evaluator.evaluate("e1", present(), "l1", T0)

    assert run_threads(8, one) == []
    for snap in snaps:
        for state in snap.values():
            assert state.pending_since_count in (0, 1)


def test_registering_the_same_lock_from_two_threads_succeeds_once():
    registry = LockRegistry()

    class SlowDict(dict):
        def __contains__(self, key):
            found = dict.__contains__(self, key)
            time.sleep(0.01)  # widen the gap between the check and the write
            return found

    registry._specs = SlowDict()
    spec = LockSpec(lock_id="same", required_keys=("a",))
    outcomes = []
    guard = threading.Lock()

    def one(i):
        try:
            registry.register(spec)
            result = "registered"
        except ValueError:
            result = "refused"
        with guard:
            outcomes.append(result)

    assert run_threads(6, one) == []
    assert outcomes.count("registered") == 1 and outcomes.count("refused") == 5


def test_listing_the_registry_while_it_grows_does_not_raise():
    registry = LockRegistry()
    stop = threading.Event()
    errors = []

    def lister():
        try:
            while not stop.is_set():
                registry.all()
                len(registry)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=lister)
    t.start()
    try:
        for i in range(300):
            registry.register(LockSpec(lock_id=f"l{i}", required_keys=("a",)))
    finally:
        stop.set()
        t.join(timeout=30)
    assert errors == []
    assert len(registry) == 300


def test_one_thread_behaves_exactly_as_before():
    spec = LockSpec(lock_id="l1", required_keys=("a",), dwell_threshold=2, combination=Combination.OR)
    evaluator = LockEvaluator(LockRegistry([spec]))
    first = evaluator.evaluate("e1", present(), "l1", T0)
    second = evaluator.evaluate("e1", present(), "l1", T0)
    assert (first.open, first.changed) == (False, False)
    assert (second.open, second.changed) == (True, True)


def test_the_lock_is_reentrant_so_a_holder_can_call_back_in():
    store = LockStateStore()
    with store.lock:
        store.get("e1", "l1")
        store.set("e1", "l1", store.get("e1", "l1"))
        store.restore("e1", store.snapshot("e1"))


@pytest.mark.parametrize("name", ["lock"])
def test_the_store_lock_is_public_and_named(name):
    assert hasattr(LockStateStore(), name)
