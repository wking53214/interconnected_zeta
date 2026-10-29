"""An observation older than the newest one a lock has applied is refused.

Without the guard a late replay of old readings counted as fresh confirmations
and could open or close a lock against newer evidence. The refusal is loud (a
LockResult marked ``stale`` with a reason) and leaves the lock state untouched.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from zeta import Key, KeySet, LockEvaluator, LockRegistry, LockSpec, LockState
from zeta.cns_connector import lock_result_content, snapshot_content

T = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
NAMES = ("fever", "hr")


def keys(*present):
    return KeySet([Key(n, n in present) for n in NAMES])


ABNORMAL = keys("fever", "hr")
NORMAL = keys()


def evaluator(**spec_kwargs):
    spec = LockSpec("sepsis_lock", NAMES, **spec_kwargs)
    return LockEvaluator(LockRegistry([spec]))


def ev(e, k, t, entity="p1"):
    return e.evaluate(entity, k, "sepsis_lock", t)


def test_old_abnormal_readings_cannot_complete_a_dwell():
    e = evaluator(dwell_threshold=3)
    assert not ev(e, ABNORMAL, T).stale
    r = ev(e, ABNORMAL, T - timedelta(hours=3))
    assert r.stale and not r.changed and not r.open
    r = ev(e, ABNORMAL, T - timedelta(hours=4))
    assert r.stale and not r.open
    assert "Stale observation" in r.reasons[0]
    assert e.state_store.get("p1", "sepsis_lock").pending_since_count == 1, "the stale ones did not count"


def test_late_abnormal_replay_cannot_open_a_lock_after_fresh_recovery():
    e = evaluator(dwell_threshold=3)
    ev(e, NORMAL, T)
    ev(e, NORMAL, T + timedelta(minutes=1))
    for minutes in (0, 1, 2):
        r = ev(e, ABNORMAL, T - timedelta(hours=2) + timedelta(minutes=minutes))
        assert r.stale and not r.open and not r.changed


def test_late_normal_replay_cannot_close_an_open_lock():
    e = evaluator(dwell_threshold=2)
    ev(e, ABNORMAL, T)
    assert ev(e, ABNORMAL, T + timedelta(minutes=1)).open
    for minutes in (0, 1):
        r = ev(e, NORMAL, T - timedelta(hours=3) + timedelta(minutes=minutes))
        assert r.stale and r.open and not r.changed


def test_a_stale_observation_leaves_the_whole_state_unchanged():
    e = evaluator(dwell_threshold=3)
    ev(e, ABNORMAL, T)
    before = e.state_store.snapshot("p1")
    ev(e, NORMAL, T - timedelta(minutes=5))
    assert e.state_store.snapshot("p1") == before


def test_an_observation_at_the_same_time_is_not_stale():
    e = evaluator(dwell_threshold=2)
    assert not ev(e, ABNORMAL, T).stale
    r = ev(e, ABNORMAL, T)
    assert not r.stale and r.open and r.changed


def test_in_order_streams_behave_exactly_as_before():
    e = evaluator(dwell_threshold=2)
    results = [ev(e, ABNORMAL, T + timedelta(seconds=i)) for i in range(3)]
    assert [r.stale for r in results] == [False, False, False]
    assert results[1].open and results[1].changed


def test_the_guard_covers_a_forced_lock_in_both_directions():
    e = evaluator(force=True)
    assert ev(e, ABNORMAL, T).forced
    r = ev(e, NORMAL, T - timedelta(hours=1))
    assert r.stale and r.open and r.forced


def test_a_stale_reading_during_cooldown_is_named_stale_not_swallowed():
    e = evaluator(lock_seconds=600)
    ev(e, ABNORMAL, T)
    r = ev(e, NORMAL, T - timedelta(hours=3))
    assert r.stale and "Stale observation" in r.reasons[0]
    r = ev(e, NORMAL, T + timedelta(minutes=5))
    assert not r.stale and "In cooldown" in r.reasons[0], "a fresh reading in cooldown is still cooldown"


def test_entities_and_locks_are_independent():
    spec_a = LockSpec("a", NAMES)
    spec_b = LockSpec("b", NAMES)
    e = LockEvaluator(LockRegistry([spec_a, spec_b]))
    e.evaluate("p1", ABNORMAL, "a", T)
    assert not e.evaluate("p2", ABNORMAL, "a", T - timedelta(hours=1)).stale, "another patient"
    assert not e.evaluate("p1", ABNORMAL, "b", T - timedelta(hours=1)).stale, "another lock"
    results = e.evaluate_all("p1", ABNORMAL, T - timedelta(minutes=1))
    assert results["a"].stale, "a saw T"
    assert not results["b"].stale, "b only saw T - 1h, so T - 1 min is fresh"


def test_the_guard_survives_a_snapshot_and_restore():
    e = evaluator(dwell_threshold=3)
    ev(e, ABNORMAL, T)
    fresh = evaluator(dwell_threshold=3)
    fresh.state_store.restore("p1", e.state_store.snapshot("p1"))
    assert ev(fresh, ABNORMAL, T - timedelta(hours=1)).stale


def test_mixing_naive_and_aware_timestamps_is_a_loud_error():
    e = evaluator()
    ev(e, ABNORMAL, T)
    with pytest.raises(ValueError, match="timezone-aware or all naive"):
        ev(e, ABNORMAL, datetime(2026, 10, 29, 12, 5))


def test_stale_marks_the_cns_bound_content_and_nothing_else_changes_it():
    e = evaluator(dwell_threshold=3)
    fresh = ev(e, ABNORMAL, T)
    stale = ev(e, ABNORMAL, T - timedelta(hours=1))
    assert "stale" not in lock_result_content(fresh), "non-stale digests are unchanged"
    assert lock_result_content(stale)["stale"] is True
    assert lock_result_content(replace(stale, stale=False)) != lock_result_content(stale)


def test_last_observed_at_is_not_part_of_the_bound_snapshot_content():
    a = LockState(open=True)
    b = replace(a, last_observed_at=T)
    assert snapshot_content({"sepsis_lock": a}) == snapshot_content({"sepsis_lock": b})
