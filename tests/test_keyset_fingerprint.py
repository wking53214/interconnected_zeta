"""A LockResult records which KeySet it was evaluated from.

Before this, a result carried no reference to its KeySet, so a connector binding it had to be TOLD which KeySet digest
to name, and a result evaluated from one set of readings could be bound to another's digest without anything noticing
(two sets of readings listing the same present Keys are the case that matters: the same Keys open the same Locks).
Zeta now fingerprints the KeySet it evaluates (a zeta-native SHA-256 over every Key's name, presence, confidence and
reason, and min_confidence) and puts it in the result it returns, on every path.
"""
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from zeta import Combination, Key, KeySet, LockEvaluator, LockRegistry, LockSpec

T0 = datetime(2026, 1, 1, 12, 0, 0)


def keys(*items, min_confidence=0.0):
    return KeySet([Key(**i) for i in items], min_confidence=min_confidence)


def sick(reason="HR 148"):
    return keys(dict(name="septic_shock", present=True, confidence=1.0, reason=reason),
                dict(name="fever", present=False, confidence=1.0, reason="Temp 37.0"))


def registry():
    return LockRegistry([
        LockSpec(lock_id="sepsis_lock", required_keys=("septic_shock",), combination=Combination.OR, lock_seconds=3600),
        LockSpec(lock_id="slow_lock", required_keys=("septic_shock",), dwell_threshold=3),
    ])


# --- the fingerprint -------------------------------------------------------------------------------------------

def test_a_fingerprint_is_a_64_character_lowercase_sha256():
    fp = sick().fingerprint()
    assert len(fp) == 64 and fp == fp.lower() and int(fp, 16) >= 0


def test_the_fingerprint_does_not_depend_on_the_order_keys_were_added():
    a = keys(dict(name="a", present=True), dict(name="b", present=False))
    b = keys(dict(name="b", present=False), dict(name="a", present=True))
    assert a.fingerprint() == b.fingerprint()


@pytest.mark.parametrize("other", [
    keys(dict(name="septic_shock", present=True, confidence=1.0, reason="HR 149"), dict(name="fever", present=False, reason="Temp 37.0")),
    keys(dict(name="septic_shock", present=True, confidence=0.9, reason="HR 148"), dict(name="fever", present=False, reason="Temp 37.0")),
    keys(dict(name="septic_shock", present=False, confidence=1.0, reason="HR 148"), dict(name="fever", present=False, reason="Temp 37.0")),
    keys(dict(name="septic_shock", present=True, confidence=1.0, reason="HR 148"), dict(name="fever", present=False, reason="Temp 37.0"), min_confidence=0.5),
    keys(dict(name="septic_shock", present=True, confidence=1.0, reason="HR 148")),
])
def test_any_difference_in_a_key_or_the_threshold_changes_the_fingerprint(other):
    assert sick(reason="HR 148").fingerprint() != other.fingerprint()


def test_two_sets_of_readings_listing_the_same_present_keys_have_different_fingerprints():
    a, b = sick("HR 148"), sick("HR 149")
    assert a.names_present() == b.names_present() and a.fingerprint() != b.fingerprint()


def test_an_integer_and_a_float_confidence_are_the_same_key():
    a = keys(dict(name="k", present=True, confidence=1))
    b = keys(dict(name="k", present=True, confidence=1.0))
    assert a.fingerprint() == b.fingerprint()


def test_a_set_edited_after_construction_no_longer_matches_a_fingerprint_taken_before():
    ks = sick()
    before = ks.fingerprint()
    ks._by_name["septic_shock"] = Key(name="septic_shock", present=False, confidence=1.0, reason="HR 148")
    assert ks.fingerprint() != before


# --- the result records it, on every path ----------------------------------------------------------------------

def test_every_result_carries_the_fingerprint_of_the_keyset_it_was_evaluated_from():
    ev = LockEvaluator(registry())
    ks = sick()
    for result in ev.evaluate_all("p1", ks, T0).values():
        assert result.keyset_fingerprint == ks.fingerprint()


def test_the_fingerprint_is_recorded_on_the_stale_and_cooldown_and_forced_paths_too():
    reg = LockRegistry([LockSpec(lock_id="l", required_keys=("septic_shock",), combination=Combination.OR,
                                 lock_seconds=3600, force=True)])
    ev = LockEvaluator(reg)
    ks = sick()
    first = ev.evaluate("p1", ks, "l", T0)                                   # forced open
    cooling = ev.evaluate("p1", ks, "l", T0 + timedelta(seconds=10))         # cooldown latch
    stale = ev.evaluate("p1", ks, "l", T0 - timedelta(hours=1))              # refused as stale
    assert first.forced and "cooldown" in cooling.reasons[0].lower() and stale.stale
    assert {first.keyset_fingerprint, cooling.keyset_fingerprint, stale.keyset_fingerprint} == {ks.fingerprint()}


def test_dwell_pending_and_unchanged_paths_record_it():
    ev = LockEvaluator(registry())
    ks = sick()
    pending = ev.evaluate("p1", ks, "slow_lock", T0)
    again = ev.evaluate("p1", ks, "slow_lock", T0 + timedelta(seconds=1))
    assert not pending.changed and not again.changed
    assert pending.keyset_fingerprint == again.keyset_fingerprint == ks.fingerprint()


def test_results_from_different_readings_record_different_fingerprints_even_when_the_lock_state_is_identical():
    r1 = LockEvaluator(registry()).evaluate("p1", sick("HR 148"), "sepsis_lock", T0)
    r2 = LockEvaluator(registry()).evaluate("p1", sick("HR 149"), "sepsis_lock", T0)
    assert (r1.open, r1.reasons) == (r2.open, r2.reasons) and r1.keyset_fingerprint != r2.keyset_fingerprint


def test_a_result_built_by_hand_records_none_and_says_so():
    from zeta import LockResult

    assert LockResult(lock_id="l", open=True, changed=False, forced=False).keyset_fingerprint == ""


def test_replacing_the_fingerprint_on_a_result_is_visible_to_a_comparison():
    r = LockEvaluator(registry()).evaluate("p1", sick(), "sepsis_lock", T0)
    assert replace(r, keyset_fingerprint="0" * 64) != r
