"""zeta binds a LockResult to one pipeline run and to the exact KeySet it evaluated.

A LockResult does not record which entity or run it is about, and nothing in it names the KeySet it came from, so
two entities with identical results bound alike and "this result came from that KeySet" could not be shown.
Context and keyset_digest are optional and present only when supplied, so every digest made without them is
unchanged (held by test_cns_connector.py).

These skip where ``cns`` is not installed. CI installs the extra and sets ZETA_REQUIRE_CNS=1, so there they cannot skip.
"""
import os
from datetime import datetime

import pytest

if os.environ.get("ZETA_REQUIRE_CNS") == "1":  # CI: a missing cns is a failure, not a skip
    import cns.gate as gate
else:
    gate = pytest.importorskip("cns.gate")

from zeta import Combination, Key, KeySet, LockEvaluator, LockRegistry, LockSpec  # noqa: E402
from zeta.cns_connector import (  # noqa: E402
    CONTEXT_SCHEMA, LOCK_RESULT_SUBJECT, LOCK_SNAPSHOT_SUBJECT, binds, lock_result_content, lock_result_digest,
    snapshot_content, snapshot_digest)

T0 = datetime(2026, 1, 1, 12, 0, 0)
CTX = {"schema": CONTEXT_SCHEMA, "entity_id": "p1", "event_id": "ev-1", "run_id": "run-1"}


def d(label):
    return gate.subject_digest({"label": label})


def evaluated():
    registry = LockRegistry([LockSpec(lock_id="sepsis_lock", required_keys=("septic_shock",), combination=Combination.OR)])
    store = LockEvaluator(registry)
    result = store.evaluate("p1", KeySet([Key(name="septic_shock", present=True)]), "sepsis_lock", T0)
    return store, result


def test_without_a_context_the_content_carries_no_context_or_lineage():
    _, result = evaluated()
    assert not {"context", "keyset_digest"} & set(lock_result_content(result))
    assert "context" not in snapshot_content({})


def test_with_a_context_the_content_names_the_run_and_the_keyset():
    _, result = evaluated()
    content = lock_result_content(result, context=dict(CTX), keyset_digest=d("keys"))
    assert content["context"] == CTX and content["keyset_digest"] == d("keys")
    assert content["entity_id"] == "p1"  # the existing field now says what the context says


@pytest.mark.parametrize("change", [
    {"context": {**CTX, "event_id": "ev-2"}},
    {"context": {**CTX, "run_id": "run-2"}},
    {"context": {**CTX, "entity_id": "p2"}},
    {"keyset_digest": d("a different keyset")},
])
def test_any_change_to_the_context_or_the_keyset_changes_the_digest(change):
    _, result = evaluated()
    base = dict(context=dict(CTX), keyset_digest=d("keys"))
    assert lock_result_digest(result, **base) != lock_result_digest(result, **{**base, **change})


def test_the_same_result_bound_to_two_runs_no_longer_binds_alike():
    _, result = evaluated()
    assert lock_result_digest(result, entity_id="p1") == lock_result_digest(result, entity_id="p1")
    a = lock_result_digest(result, context=dict(CTX), keyset_digest=d("keys"))
    b = lock_result_digest(result, context={**CTX, "run_id": "run-2"}, keyset_digest=d("keys"))
    assert a != b


def test_an_entity_id_that_disagrees_with_the_context_is_refused():
    _, result = evaluated()
    with pytest.raises(ValueError, match="entity_id 'p2' disagrees with the context's 'p1'"):
        lock_result_content(result, entity_id="p2", context=dict(CTX), keyset_digest=d("keys"))
    assert lock_result_content(result, entity_id="p1", context=dict(CTX), keyset_digest=d("keys"))["entity_id"] == "p1"


def test_a_context_without_its_keyset_digest_is_refused_and_so_is_the_reverse():
    _, result = evaluated()
    with pytest.raises(ValueError, match="a context needs keyset_digest"):
        lock_result_content(result, context=dict(CTX))
    with pytest.raises(ValueError, match="keyset_digest needs a context"):
        lock_result_content(result, keyset_digest=d("keys"))


@pytest.mark.parametrize("bad, exc, match", [
    ({k: v for k, v in CTX.items() if k != "event_id"}, ValueError, "exactly"),
    ({**CTX, "extra": "x"}, ValueError, "exactly"),
    ({**CTX, "entity_id": ""}, ValueError, "non-empty"),
    ({**CTX, "event_id": None}, TypeError, "must be a str"),
    ({**CTX, "schema": "spine-context/0"}, ValueError, "unsupported context schema"),
    ([("run_id", "x")], TypeError, "mapping"),
])
def test_a_malformed_context_is_refused(bad, exc, match):
    _, result = evaluated()
    with pytest.raises(exc, match=match):
        lock_result_content(result, context=bad, keyset_digest=d("keys"))


@pytest.mark.parametrize("bad, exc", [("F" * 64, ValueError), ("deadbeef", ValueError), (123, TypeError)])
def test_a_malformed_keyset_digest_is_refused(bad, exc):
    _, result = evaluated()
    with pytest.raises(exc):
        lock_result_content(result, context=dict(CTX), keyset_digest=bad)


def test_a_snapshot_takes_a_context_and_needs_no_keyset():
    store, _ = evaluated()
    snap = store.state_store.snapshot("p1")
    content = snapshot_content(snap, context=dict(CTX))
    assert content["context"] == CTX and content["entity_id"] == "p1"
    assert snapshot_digest(snap, context=dict(CTX)) != snapshot_digest(snap, entity_id="p1")
    with pytest.raises(ValueError, match="disagrees with the context"):
        snapshot_content(snap, entity_id="p2", context=dict(CTX))


def test_binds_checks_a_verdict_issued_against_the_run_bound_result():
    _, result = evaluated()
    run = dict(context=dict(CTX), keyset_digest=d("keys"))
    verdict = gate.GateResult(gate="g", position=gate.GatePosition.OMEGA, outcome=gate.GateOutcome.PASS,
                              reason="issued for the test", subject=LOCK_RESULT_SUBJECT,
                              subject_digest=lock_result_digest(result, **run))
    assert binds(verdict, result, **run)
    assert not binds(verdict, result)
    assert not binds(verdict, result, context={**CTX, "run_id": "run-2"}, keyset_digest=d("keys"))
    snap_verdict = gate.GateResult(gate="g", position=gate.GatePosition.OMEGA, outcome=gate.GateOutcome.PASS,
                                   reason="x", subject=LOCK_SNAPSHOT_SUBJECT,
                                   subject_digest=snapshot_digest({}, context=dict(CTX)))
    assert binds(snap_verdict, {}, context=dict(CTX))
