"""Many threads, many entities, one registry and one store: the results equal a single-thread run, and nothing deadlocks.

test_threads.py proves each guard with a deliberately slowed step. These tests prove the same property under volume, with the interpreter
forced to switch threads as often as it can, so a missing lock shows up as a wrong count or a hang instead of passing by luck.
"""
import sys
import threading
from datetime import datetime

import pytest

from zeta import Key, KeySet, LockEvaluator, LockRegistry, LockSpec, LockStateStore

T0 = datetime(2026, 1, 1, 12, 0, 0)
JOIN_SECONDS = 60


@pytest.fixture(autouse=True)
def switch_threads_constantly():
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(old)


def keys(present):
    return KeySet([Key(name="a", present=present)])


def run_threads(count, target):
    errors = []
    ready = threading.Barrier(count)

    def wrapped(index):
        try:
            ready.wait(timeout=JOIN_SECONDS)
            target(index)
        except BaseException as exc:  # noqa: BLE001 - the test reports what a thread raised
            errors.append(repr(exc))

    threads = [threading.Thread(target=wrapped, args=(i,), daemon=True) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_SECONDS)
    assert not any(t.is_alive() for t in threads), "a thread did not finish: a deadlock or a hang"
    return errors


def specs(n=3, dwell=3):
    return [LockSpec(lock_id=f"l{i}", required_keys=("a",), dwell_threshold=dwell) for i in range(n)]


def sequence_for(entity_index, rounds):
    """A deterministic pattern per entity: present for a while, then absent, so locks open and close."""
    return [((entity_index + r) // 4) % 2 == 0 for r in range(rounds)]


def single_thread_trace(entities, rounds):
    evaluator = LockEvaluator(LockRegistry(specs()), LockStateStore())
    trace = {}
    for e in range(entities):
        trace[e] = [[(res.open, res.changed) for _, res in sorted(evaluator.evaluate_all(f"e{e}", keys(p), T0).items())]
                    for p in sequence_for(e, rounds)]
    return trace, evaluator.state_store


def test_many_entities_on_many_threads_equal_a_single_thread_run():
    entities, rounds, threads = 48, 24, 8
    expected, expected_store = single_thread_trace(entities, rounds)
    store = LockStateStore()
    evaluator = LockEvaluator(LockRegistry(specs()), store)
    got = {}
    guard = threading.Lock()

    def worker(index):
        for e in range(index, entities, threads):  # each entity belongs to one thread, so its own order is fixed
            trace = [[(res.open, res.changed) for _, res in sorted(evaluator.evaluate_all(f"e{e}", keys(p), T0).items())]
                     for p in sequence_for(e, rounds)]
            with guard:
                got[e] = trace

    assert run_threads(threads, worker) == []
    assert got == expected
    for e in range(entities):
        assert store.snapshot(f"e{e}") == expected_store.snapshot(f"e{e}")


def test_one_entity_hit_by_every_thread_counts_each_confirmation_once():
    threads, each, dwell = 8, 40, 200  # 320 confirmations, dwell 200: the lock opens once, on the 200th
    spec = LockSpec(lock_id="l", required_keys=("a",), dwell_threshold=dwell)
    store = LockStateStore()
    evaluator = LockEvaluator(LockRegistry([spec]), store)
    results = []
    guard = threading.Lock()

    def worker(index):
        for _ in range(each):
            r = evaluator.evaluate("shared", keys(True), "l", T0)
            with guard:
                results.append(r)

    assert run_threads(threads, worker) == []
    assert len(results) == threads * each
    assert sum(1 for r in results if r.changed) == 1
    assert sum(1 for r in results if r.open) == threads * each - (dwell - 1)
    assert store.get("shared", "l").open is True


def test_registering_while_evaluating_neither_deadlocks_nor_raises():
    registry = LockRegistry(specs(2))
    store = LockStateStore()
    evaluator = LockEvaluator(registry, store)

    def worker(index):
        if index == 0:
            for i in range(150):
                registry.register(LockSpec(lock_id=f"extra{i}", required_keys=("a",)))
        elif index == 1:
            for _ in range(150):
                store.snapshot("e0")
                len(registry)
                registry.all()
        else:
            for _ in range(60):
                out = evaluator.evaluate_all("e0", keys(True), T0)
                assert {"l0", "l1"} <= set(out)

    assert run_threads(6, worker) == []
    assert len(registry) == 152


def test_two_concurrent_runs_end_in_the_same_state():
    def run():
        store = LockStateStore()
        evaluator = LockEvaluator(LockRegistry(specs(4, dwell=5)), store)

        def worker(index):
            for r in range(20):
                evaluator.evaluate_all(f"e{index}", keys(r % 7 != 0), T0)

        assert run_threads(8, worker) == []
        return {f"e{i}": store.snapshot(f"e{i}") for i in range(8)}

    assert run() == run()
