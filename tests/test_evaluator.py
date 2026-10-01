from datetime import datetime, timedelta

import pytest

from zeta import Combination, Key, KeySet, LockEvaluator, LockRegistry, LockSpec, LockStateStore

T0 = datetime(2026, 1, 1, 12, 0, 0)


def keys_for(*present_names, absent_names=()):
    ks = [Key(name=n, present=True) for n in present_names]
    ks += [Key(name=n, present=False) for n in absent_names]
    return KeySet(ks)


def make_evaluator(spec):
    registry = LockRegistry([spec])
    return LockEvaluator(registry)


# --- No-debounce (dwell_threshold=1): opens on first satisfying observation ---

def test_opens_immediately_with_dwell_threshold_one():
    spec = LockSpec(lock_id="l1", required_keys=("fever",), dwell_threshold=1)
    ev = make_evaluator(spec)

    result = ev.evaluate("patient1", keys_for("fever"), "l1", T0)

    assert result.open is True
    assert result.changed is True
    assert result.forced is False


def test_stays_closed_without_satisfying_keys():
    spec = LockSpec(lock_id="l1", required_keys=("fever",), dwell_threshold=1)
    ev = make_evaluator(spec)

    result = ev.evaluate("patient1", keys_for(absent_names=("fever",)), "l1", T0)

    assert result.open is False
    assert result.changed is False


# --- Symmetric dwell debounce: opening ---

def test_dwell_threshold_two_requires_two_consecutive_observations_to_open():
    spec = LockSpec(lock_id="sepsis_lock", required_keys=("fever", "tachycardia"), dwell_threshold=2)
    ev = make_evaluator(spec)

    r1 = ev.evaluate("patient1", keys_for("fever", "tachycardia"), "sepsis_lock", T0)
    assert r1.open is False
    assert r1.changed is False
    assert "1/2" in r1.reasons[0]

    r2 = ev.evaluate("patient1", keys_for("fever", "tachycardia"), "sepsis_lock", T0 + timedelta(seconds=10))
    assert r2.open is True
    assert r2.changed is True


def test_dwell_resets_when_observation_stops_matching_pending():
    spec = LockSpec(lock_id="l1", required_keys=("fever",), dwell_threshold=3)
    ev = make_evaluator(spec)

    r1 = ev.evaluate("patient1", keys_for("fever"), "l1", T0)
    assert r1.open is False  # pending_since_count = 1

    # Observation reverts to "closed matches confirmed state" -> resets pending
    r2 = ev.evaluate("patient1", keys_for(absent_names=("fever",)), "l1", T0 + timedelta(seconds=1))
    assert r2.open is False
    assert r2.changed is False

    # New satisfying observation starts dwell count over at 1, not 2
    r3 = ev.evaluate("patient1", keys_for("fever"), "l1", T0 + timedelta(seconds=2))
    assert r3.open is False
    assert "1/3" in r3.reasons[0]


# --- Symmetric dwell debounce: closing (the nuance found in the original private implementation) ---

def test_closing_requires_same_dwell_threshold_as_opening():
    spec = LockSpec(lock_id="l1", required_keys=("fever",), dwell_threshold=2)
    ev = make_evaluator(spec)

    # Open it (two consecutive satisfying observations).
    ev.evaluate("patient1", keys_for("fever"), "l1", T0)
    r_open = ev.evaluate("patient1", keys_for("fever"), "l1", T0 + timedelta(seconds=1))
    assert r_open.open is True

    # One non-satisfying observation should NOT immediately close it.
    r_pending_close = ev.evaluate("patient1", keys_for(absent_names=("fever",)), "l1", T0 + timedelta(seconds=2))
    assert r_pending_close.open is True, "single normal reading must not immediately flip lock closed"
    assert r_pending_close.changed is False

    # A second consecutive non-satisfying observation confirms the close.
    r_closed = ev.evaluate("patient1", keys_for(absent_names=("fever",)), "l1", T0 + timedelta(seconds=3))
    assert r_closed.open is False
    assert r_closed.changed is True


# --- Cooldown latch ---

def test_cooldown_latch_ignores_input_until_lock_seconds_elapses():
    spec = LockSpec(lock_id="l1", required_keys=("fever",), dwell_threshold=1, lock_seconds=300)
    ev = make_evaluator(spec)

    r_open = ev.evaluate("patient1", keys_for("fever"), "l1", T0)
    assert r_open.open is True

    # 100 seconds later, key no longer present -- should be ignored (still in cooldown).
    r_during_cooldown = ev.evaluate("patient1", keys_for(absent_names=("fever",)), "l1", T0 + timedelta(seconds=100))
    assert r_during_cooldown.open is True
    assert r_during_cooldown.changed is False
    assert "cooldown" in r_during_cooldown.reasons[0]

    # After lock_seconds elapses, normal processing resumes.
    r_after_cooldown = ev.evaluate("patient1", keys_for(absent_names=("fever",)), "l1", T0 + timedelta(seconds=301))
    assert r_after_cooldown.open is False
    assert r_after_cooldown.changed is True


def test_cooldown_only_applies_after_lock_seconds_configured():
    spec = LockSpec(lock_id="l1", required_keys=("fever",), dwell_threshold=1, lock_seconds=0)
    ev = make_evaluator(spec)

    ev.evaluate("patient1", keys_for("fever"), "l1", T0)
    # No cooldown configured -> immediately reflects the next observation.
    r = ev.evaluate("patient1", keys_for(absent_names=("fever",)), "l1", T0 + timedelta(seconds=1))
    assert r.open is False
    assert r.changed is True


# --- Force bypass ---

def test_force_opens_immediately_even_with_high_dwell_threshold():
    spec = LockSpec(lock_id="bypass_lock", required_keys=("hard_rule",), dwell_threshold=10, force=True)
    ev = make_evaluator(spec)

    result = ev.evaluate("patient1", keys_for("hard_rule"), "bypass_lock", T0)

    assert result.open is True
    assert result.changed is True
    assert result.forced is True


def test_force_does_not_bypass_dwell_when_closing():
    spec = LockSpec(lock_id="bypass_lock", required_keys=("hard_rule",), dwell_threshold=2, force=True)
    ev = make_evaluator(spec)

    # Force-opens on first satisfying observation.
    r_open = ev.evaluate("patient1", keys_for("hard_rule"), "bypass_lock", T0)
    assert r_open.open is True
    assert r_open.forced is True

    # Closing still requires normal dwell confirmation (force never applies to closing).
    r1 = ev.evaluate("patient1", keys_for(absent_names=("hard_rule",)), "bypass_lock", T0 + timedelta(seconds=1))
    assert r1.open is True  # still open, pending close 1/2

    r2 = ev.evaluate("patient1", keys_for(absent_names=("hard_rule",)), "bypass_lock", T0 + timedelta(seconds=2))
    assert r2.open is False  # confirmed close on 2nd


def test_force_only_triggers_when_not_already_open():
    spec = LockSpec(lock_id="l1", required_keys=("fever",), dwell_threshold=1, force=True)
    ev = make_evaluator(spec)

    r1 = ev.evaluate("patient1", keys_for("fever"), "l1", T0)
    assert r1.changed is True

    # Already open + still satisfied -> no re-triggering, changed=False.
    r2 = ev.evaluate("patient1", keys_for("fever"), "l1", T0 + timedelta(seconds=1))
    assert r2.open is True
    assert r2.changed is False


# --- Isolation between entities and between locks ---

def test_state_isolated_per_entity():
    spec = LockSpec(lock_id="l1", required_keys=("fever",), dwell_threshold=1)
    ev = make_evaluator(spec)

    r_patient1 = ev.evaluate("patient1", keys_for("fever"), "l1", T0)
    r_patient2 = ev.evaluate("patient2", keys_for(absent_names=("fever",)), "l1", T0)

    assert r_patient1.open is True
    assert r_patient2.open is False


def test_state_isolated_per_lock_for_same_entity():
    registry = LockRegistry([
        LockSpec(lock_id="sepsis_lock", required_keys=("fever", "tachycardia"), dwell_threshold=1),
        LockSpec(lock_id="discharge_lock", required_keys=("alert", "feeding"), dwell_threshold=1),
    ])
    ev = LockEvaluator(registry)

    keys = keys_for("alert", "feeding", absent_names=("fever", "tachycardia"))
    results = ev.evaluate_all("patient1", keys, T0)

    assert results["discharge_lock"].open is True
    assert results["sepsis_lock"].open is False


# --- Determinism ---

def test_identical_input_sequence_yields_identical_results():
    def run():
        spec = LockSpec(lock_id="l1", required_keys=("fever",), dwell_threshold=2, lock_seconds=60)
        ev = LockEvaluator(LockRegistry([spec]))
        outcomes = []
        for i, present in enumerate([True, True, False, False, True]):
            ts = T0 + timedelta(seconds=i * 30)
            k = keys_for("fever") if present else keys_for(absent_names=("fever",))
            outcomes.append(ev.evaluate("patient1", k, "l1", ts).open)
        return outcomes

    assert run() == run()


# --- N_OF_M combination in the evaluator (not just LockSpec unit) ---

def test_n_of_m_lock_opens_when_threshold_met():
    spec = LockSpec(
        lock_id="warning_lock",
        required_keys=("fever", "tachycardia", "tachypnea", "altered_mental_status"),
        combination=Combination.N_OF_M,
        n=2,
        dwell_threshold=1,
    )
    ev = make_evaluator(spec)

    result = ev.evaluate("patient1", keys_for("fever", "tachycardia"), "warning_lock", T0)
    assert result.open is True


def test_n_of_m_lock_stays_closed_below_threshold():
    spec = LockSpec(
        lock_id="warning_lock",
        required_keys=("fever", "tachycardia", "tachypnea", "altered_mental_status"),
        combination=Combination.N_OF_M,
        n=2,
        dwell_threshold=1,
    )
    ev = make_evaluator(spec)

    result = ev.evaluate("patient1", keys_for("fever"), "warning_lock", T0)
    assert result.open is False


# --- Unknown lock_id ---

def test_evaluate_unknown_lock_raises():
    registry = LockRegistry([LockSpec(lock_id="l1", required_keys=("a",))])
    ev = LockEvaluator(registry)
    with pytest.raises(KeyError):
        ev.evaluate("patient1", keys_for("a"), "does_not_exist", T0)
