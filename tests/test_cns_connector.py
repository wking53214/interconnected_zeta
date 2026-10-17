"""zeta's outputs made bindable to a CNS verdict (binding only, no gate).

These skip where ``cns`` is not installed. CI installs the extra and sets
ZETA_REQUIRE_CNS=1, so there they cannot skip.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest

if os.environ.get("ZETA_REQUIRE_CNS") == "1":  # CI: a missing cns is a failure, not a skip
    import cns.gate as gate
else:
    gate = pytest.importorskip("cns.gate")

from zeta import Combination, Key, KeySet, LockEvaluator, LockRegistry, LockResult, LockSpec, LockState  # noqa: E402
from zeta.cns_connector import (  # noqa: E402
    LOCK_RESULT_SUBJECT, LOCK_SNAPSHOT_SUBJECT, binds, lock_result_content, lock_result_digest,
    snapshot_content, snapshot_digest)

T0 = datetime(2026, 1, 1, 12, 0, 0)


def issued(subject, digest, **kw):
    """A verdict as a gate would issue it: bound to what it judged. The outcome is irrelevant to binding."""
    return gate.GateResult(gate="test", position=gate.GatePosition.OMEGA, outcome=gate.GateOutcome.PASS,
                           subject=subject, subject_digest=digest, **kw)


def hand_built():
    return gate.GateResult(gate="test", position=gate.GatePosition.OMEGA, outcome=gate.GateOutcome.PASS)


def a_result(**over):
    base = dict(lock_id="sepsis_lock", open=True, changed=True, forced=False, reasons=["opened", "after dwell"])
    base.update(over)
    return LockResult(**base)


def a_state(**over):
    base = dict(open=True, pending_open=False, pending_since_count=2, locked_until=T0 + timedelta(hours=1),
                opened_at=T0, forced=False)
    base.update(over)
    return LockState(**base)


def a_snapshot():
    return {"sepsis_lock": a_state(), "discharge_lock": a_state(open=False, pending_open=None, opened_at=None,
                                                                locked_until=None, pending_since_count=0)}


def real_evaluation():
    """Evaluate a real lock with the real evaluator: dwell, then force, then cooldown."""
    registry = LockRegistry([
        LockSpec(lock_id="sepsis_lock", required_keys=("septic_shock", "fever"), combination=Combination.OR,
                 force=True, lock_seconds=3600),
        LockSpec(lock_id="discharge_lock", required_keys=("stable",), dwell_threshold=2),
    ])
    ev = LockEvaluator(registry)
    keys = KeySet([Key("septic_shock", True, 1.0, "HR>140"), Key("fever", False), Key("stable", True)])
    results = ev.evaluate_all("patient_1", keys, T0)
    return ev, results


# ------------------------------------------------------------ content shape

def test_a_lock_result_content_names_every_field_and_nothing_volatile():
    assert lock_result_content(a_result()) == {
        "entity_id": None, "lock_id": "sepsis_lock", "open": True, "changed": True, "forced": False,
        "reasons": ["opened", "after dwell"]}


def test_a_snapshot_content_names_every_state_field():
    assert snapshot_content({"l": a_state()}, entity_id="p1") == {
        "entity_id": "p1",
        "locks": {"l": {"open": True, "pending_open": False, "pending_since_count": 2,
                        "locked_until": "2026-01-01T13:00:00", "opened_at": "2026-01-01T12:00:00",
                        "forced": False}}}


def test_content_is_json_like_and_cns_accepts_it():
    for content in (lock_result_content(a_result(), entity_id="p"), snapshot_content(a_snapshot(), entity_id="p"),
                    snapshot_content({}), lock_result_content(a_result(reasons=[]))):
        assert isinstance(gate.subject_digest(content), str)

        def only_json(v):
            if isinstance(v, dict):
                assert all(isinstance(k, str) for k in v) and all(only_json(x) is None for x in v.values())
            elif isinstance(v, list):
                assert all(only_json(x) is None for x in v)
            else:
                assert v is None or type(v) in (str, int, float, bool), type(v)
        only_json(content)


def test_content_needs_no_cns_but_the_digest_is_cns_subject_digest():
    result = a_result()
    assert lock_result_digest(result) == gate.subject_digest(lock_result_content(result))
    assert snapshot_digest(a_snapshot()) == gate.subject_digest(snapshot_content(a_snapshot()))


# ------------------------------------------------------------ determinism and irrelevant differences

def test_the_digest_is_deterministic_across_equal_but_distinct_objects():
    assert lock_result_digest(a_result()) == lock_result_digest(a_result())
    assert a_result() is not a_result()
    assert snapshot_digest(a_snapshot()) == snapshot_digest(a_snapshot())
    assert lock_result_digest(a_result(), entity_id="p") == lock_result_digest(a_result(), entity_id="p")


def test_the_order_locks_are_listed_in_a_snapshot_cannot_change_the_digest():
    forward = a_snapshot()
    backward = dict(reversed(list(forward.items())))
    assert list(forward) != list(backward)
    assert snapshot_digest(forward) == snapshot_digest(backward)


def test_reasons_given_as_a_tuple_digest_like_the_same_list():
    assert lock_result_digest(a_result(reasons=("a", "b"))) == lock_result_digest(a_result(reasons=["a", "b"]))


def test_a_snapshot_is_the_value_not_the_live_objects():
    ev, _ = real_evaluation()
    snap = ev.state_store.snapshot("patient_1")
    before = snapshot_digest(snap, entity_id="patient_1")
    ev.evaluate("patient_1", KeySet([Key("septic_shock", False), Key("fever", False), Key("stable", False)]),
                "discharge_lock", T0 + timedelta(hours=5))
    assert snapshot_digest(snap, entity_id="patient_1") == before          # the taken snapshot did not move
    assert snapshot_digest(ev.state_store.snapshot("patient_1"), entity_id="patient_1") != before   # the live state did


# ------------------------------------------------------------ every bound field matters

@pytest.mark.parametrize("over", [
    {"lock_id": "other_lock"}, {"open": False}, {"changed": False}, {"forced": True},
    {"reasons": ["opened"]}, {"reasons": ["opened", "after dwell", "extra"]}, {"reasons": ["after dwell", "opened"]},
    {"reasons": ["opened", "after dwell "]}, {"reasons": []}])
def test_changing_any_lock_result_field_changes_the_digest(over):
    assert lock_result_digest(a_result(**over)) != lock_result_digest(a_result())


def test_the_entity_is_bound_when_given_and_distinguishes_entities():
    base = lock_result_digest(a_result())
    assert lock_result_digest(a_result(), entity_id="p1") != base
    assert lock_result_digest(a_result(), entity_id="p1") != lock_result_digest(a_result(), entity_id="p2")
    assert snapshot_digest(a_snapshot(), entity_id="p1") != snapshot_digest(a_snapshot())
    assert snapshot_digest(a_snapshot(), entity_id="p1") != snapshot_digest(a_snapshot(), entity_id="p2")
    assert lock_result_digest(a_result(), entity_id="") != base            # an empty id is still an id


@pytest.mark.parametrize("over", [
    {"open": False}, {"pending_open": True}, {"pending_open": None}, {"pending_since_count": 3},
    {"locked_until": T0 + timedelta(hours=2)}, {"locked_until": None}, {"opened_at": T0 + timedelta(seconds=1)},
    {"opened_at": None}, {"forced": True}])
def test_changing_any_lock_state_field_changes_the_snapshot_digest(over):
    changed = {"sepsis_lock": a_state(**over)}
    assert snapshot_digest(changed) != snapshot_digest({"sepsis_lock": a_state()})


def test_the_snapshot_digest_follows_lock_ids_and_membership():
    base = {"sepsis_lock": a_state()}
    assert snapshot_digest({"renamed": a_state()}) != snapshot_digest(base)
    assert snapshot_digest({**base, "second": a_state()}) != snapshot_digest(base)
    assert snapshot_digest({}) != snapshot_digest(base)
    swapped = {"a": a_state(open=True), "b": a_state(open=False)}
    other = {"a": a_state(open=False), "b": a_state(open=True)}
    assert snapshot_digest(swapped) != snapshot_digest(other)              # which lock has the state matters


def test_a_time_differing_by_a_microsecond_is_a_different_record():
    whole = {"l": a_state(opened_at=datetime(2026, 1, 1, 12))}
    assert snapshot_digest(whole) != snapshot_digest({"l": a_state(opened_at=datetime(2026, 1, 1, 12, 0, 0, 1))})
    assert snapshot_digest(whole) != snapshot_digest({"l": a_state(locked_until=datetime(2026, 1, 1, 12, 0, 0, 1))})


def test_a_naive_and_an_aware_time_are_different_records():
    naive = {"l": a_state(opened_at=datetime(2026, 1, 1, 12))}
    aware = {"l": a_state(opened_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc))}
    assert snapshot_digest(naive) != snapshot_digest(aware)


# ------------------------------------------------------------ wrong types

@pytest.mark.parametrize("bad", [None, "result", {"lock_id": "x"}, LockState(), KeySet([]), 7, [a_result()]])
def test_something_that_is_not_a_lock_result_is_a_type_error(bad):
    with pytest.raises(TypeError):
        lock_result_content(bad)
    with pytest.raises(TypeError):
        lock_result_digest(bad)


@pytest.mark.parametrize("bad", [None, "snapshot", [("l", LockState())], a_result(), LockState(),
                                 {"l": a_result()}, {"l": None}, {1: LockState()}, {"l": {"open": True}}])
def test_something_that_is_not_a_snapshot_is_a_type_error(bad):
    with pytest.raises(TypeError):
        snapshot_content(bad)
    with pytest.raises(TypeError):
        snapshot_digest(bad)


@pytest.mark.parametrize("bad", [
    {"lock_id": 7}, {"reasons": "opened"}, {"reasons": None}, {"reasons": [1]}, {"reasons": ["a", None]}])
def test_a_hand_built_lock_result_with_unbindable_fields_is_a_type_error(bad):
    with pytest.raises(TypeError):
        lock_result_content(a_result(**bad))


@pytest.mark.parametrize("bad", [
    {"locked_until": "2026-01-01"}, {"opened_at": 1767268800}, {"pending_since_count": "2"},
    {"pending_since_count": 1.5}, {"pending_since_count": True}, {"pending_since_count": None}])
def test_a_hand_built_lock_state_with_unbindable_fields_is_a_type_error(bad):
    with pytest.raises(TypeError):
        snapshot_content({"l": a_state(**bad)})


@pytest.mark.parametrize("bad", [7, b"p", ["p"], 1.5])
def test_the_entity_id_must_be_text(bad):
    with pytest.raises(TypeError):
        lock_result_content(a_result(), entity_id=bad)
    with pytest.raises(TypeError):
        snapshot_content(a_snapshot(), entity_id=bad)


def test_binds_refuses_something_that_is_neither_a_result_nor_a_snapshot():
    for bad in (None, "x", 3, [a_result()], LockState(), KeySet([])):
        with pytest.raises(TypeError):
            binds(issued("zeta.lock_result", "d"), bad)


# ------------------------------------------------------------ binds

def test_a_verdict_issued_against_a_lock_result_binds_to_that_result_only():
    result = a_result()
    verdict = issued(LOCK_RESULT_SUBJECT, lock_result_digest(result))
    assert binds(verdict, result)
    assert binds(verdict, a_result())                                      # an equal result, not the same object
    assert not binds(verdict, a_result(open=False))
    assert not binds(verdict, a_result(reasons=["something else"]))
    assert not binds(verdict, a_result(lock_id="other_lock"))


def test_a_verdict_issued_against_a_snapshot_binds_to_that_snapshot_only():
    snap = a_snapshot()
    verdict = issued(LOCK_SNAPSHOT_SUBJECT, snapshot_digest(snap))
    assert binds(verdict, snap)
    assert binds(verdict, dict(reversed(list(snap.items()))))
    assert not binds(verdict, {**snap, "sepsis_lock": a_state(forced=True)})
    assert not binds(verdict, {})


def test_binds_checks_the_subject_not_just_the_digest():
    result = a_result()
    right_digest = lock_result_digest(result)
    assert not binds(issued("some.other.subject", right_digest), result)
    assert not binds(issued(LOCK_SNAPSHOT_SUBJECT, right_digest), result)  # the other type's label
    assert binds(issued("some.other.subject", right_digest), result, subject="some.other.subject")
    assert not binds(issued(LOCK_RESULT_SUBJECT, right_digest), result, subject="some.other.subject")


def test_binds_checks_the_digest_not_just_the_subject():
    assert not binds(issued(LOCK_RESULT_SUBJECT, lock_result_digest(a_result(open=False))), a_result())
    assert not binds(issued(LOCK_RESULT_SUBJECT, "0" * 64), a_result())
    assert not binds(issued(LOCK_SNAPSHOT_SUBJECT, snapshot_digest({})), a_snapshot())


def test_binds_passes_the_entity_through():
    result = a_result()
    verdict = issued(LOCK_RESULT_SUBJECT, lock_result_digest(result, entity_id="p1"))
    assert binds(verdict, result, entity_id="p1")
    assert not binds(verdict, result, entity_id="p2")
    assert not binds(verdict, result)
    snap_verdict = issued(LOCK_SNAPSHOT_SUBJECT, snapshot_digest(a_snapshot(), entity_id="p1"))
    assert binds(snap_verdict, a_snapshot(), entity_id="p1")
    assert not binds(snap_verdict, a_snapshot(), entity_id="p2")
    assert not binds(snap_verdict, a_snapshot())


def test_an_unbound_or_hand_built_verdict_binds_to_nothing():
    for obj in (a_result(), a_snapshot(), {}):
        assert not binds(hand_built(), obj)
        assert not binds(hand_built(), obj, subject="")
    assert not binds(issued("", lock_result_digest(a_result())), a_result(), subject="")
    assert not binds(issued(LOCK_RESULT_SUBJECT, ""), a_result())


def test_binds_returns_a_real_bool():
    verdict = issued(LOCK_RESULT_SUBJECT, lock_result_digest(a_result()))
    assert binds(verdict, a_result()) is True
    assert binds(hand_built(), a_result()) is False


# ------------------------------------------------------------ against the real evaluator

def test_a_verdict_on_a_real_evaluation_binds_to_it_and_not_to_the_next_one():
    ev, results = real_evaluation()
    sepsis, discharge = results["sepsis_lock"], results["discharge_lock"]
    assert sepsis.open and sepsis.forced and not discharge.open      # forced open at once; discharge still in dwell
    verdict = issued(LOCK_RESULT_SUBJECT, lock_result_digest(sepsis, entity_id="patient_1"))
    assert binds(verdict, sepsis, entity_id="patient_1")
    assert not binds(verdict, discharge, entity_id="patient_1")
    assert not binds(verdict, sepsis, entity_id="patient_2")
    # the same lock a moment later is a different record (it is now in cooldown)
    later = ev.evaluate("patient_1", KeySet([Key("septic_shock", True)]), "sepsis_lock", T0 + timedelta(minutes=5))
    assert later.reasons != sepsis.reasons and not binds(verdict, later, entity_id="patient_1")


def test_a_verdict_on_a_real_snapshot_binds_to_it_until_the_state_moves():
    ev, results = real_evaluation()
    snap = ev.state_store.snapshot("patient_1")
    assert snap["sepsis_lock"].open and snap["sepsis_lock"].forced and snap["sepsis_lock"].locked_until is not None
    verdict = issued(LOCK_SNAPSHOT_SUBJECT, snapshot_digest(snap, entity_id="patient_1"))
    assert binds(verdict, snap, entity_id="patient_1")
    ev.evaluate("patient_1", KeySet([Key("stable", True)]), "discharge_lock", T0 + timedelta(minutes=1))  # dwell met
    moved = ev.state_store.snapshot("patient_1")
    assert moved["discharge_lock"].open and not snap["discharge_lock"].open
    assert not binds(verdict, moved, entity_id="patient_1")
    assert binds(verdict, snap, entity_id="patient_1")
    # a restore round trip is the same record
    ev.state_store.restore("patient_2", snap)
    assert binds(verdict, ev.state_store.snapshot("patient_2"), entity_id="patient_1")


def test_every_lock_result_of_a_real_evaluation_round_trips_through_cns():
    _, results = real_evaluation()
    for lock_id, result in results.items():
        content = lock_result_content(result, entity_id="patient_1")
        assert content["lock_id"] == lock_id and content["reasons"] == list(result.reasons)
        assert binds(issued(LOCK_RESULT_SUBJECT, gate.subject_digest(content)), result, entity_id="patient_1")
