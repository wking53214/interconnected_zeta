import pytest

from zeta import Combination, LockSpec, LockRegistry


def test_and_combination_requires_all_keys():
    spec = LockSpec(lock_id="l1", required_keys=("a", "b", "c"), combination=Combination.AND)
    assert spec.keys_satisfied({"a", "b", "c"}) is True
    assert spec.keys_satisfied({"a", "b"}) is False
    assert spec.keys_satisfied(set()) is False


def test_or_combination_requires_any_key():
    spec = LockSpec(lock_id="l1", required_keys=("a", "b", "c"), combination=Combination.OR)
    assert spec.keys_satisfied({"a"}) is True
    assert spec.keys_satisfied({"z"}) is False
    assert spec.keys_satisfied(set()) is False


def test_n_of_m_combination_requires_n_keys():
    spec = LockSpec(lock_id="l1", required_keys=("a", "b", "c", "d"), combination=Combination.N_OF_M, n=2)
    assert spec.keys_satisfied({"a", "b"}) is True
    assert spec.keys_satisfied({"a"}) is False
    assert spec.keys_satisfied({"a", "b", "c"}) is True


def test_n_of_m_requires_n():
    with pytest.raises(ValueError):
        LockSpec(lock_id="l1", required_keys=("a", "b"), combination=Combination.N_OF_M)


def test_n_of_m_n_cannot_exceed_required_keys():
    with pytest.raises(ValueError):
        LockSpec(lock_id="l1", required_keys=("a", "b"), combination=Combination.N_OF_M, n=5)


def test_duplicate_required_keys_rejected():
    with pytest.raises(ValueError):
        LockSpec(lock_id="l1", required_keys=("a", "a"), combination=Combination.N_OF_M, n=2)


def test_n_of_m_not_satisfied_by_single_distinct_key_present_once():
    # Regression: keys_satisfied must count DISTINCT present keys, not
    # matches over a possibly-repeated required_keys tuple.
    spec = LockSpec(lock_id="l1", required_keys=("a", "b", "c"), combination=Combination.N_OF_M, n=2)
    assert spec.keys_satisfied({"a"}) is False


def test_empty_required_keys_rejected():
    with pytest.raises(ValueError):
        LockSpec(lock_id="l1", required_keys=())


def test_empty_lock_id_rejected():
    with pytest.raises(ValueError):
        LockSpec(lock_id="", required_keys=("a",))


def test_dwell_threshold_must_be_positive():
    with pytest.raises(ValueError):
        LockSpec(lock_id="l1", required_keys=("a",), dwell_threshold=0)


def test_lock_seconds_cannot_be_negative():
    with pytest.raises(ValueError):
        LockSpec(lock_id="l1", required_keys=("a",), lock_seconds=-1)


def test_registry_register_and_get():
    reg = LockRegistry()
    spec = LockSpec(lock_id="sepsis_lock", required_keys=("fever", "tachycardia"))
    reg.register(spec)
    assert reg.get("sepsis_lock") is spec
    assert "sepsis_lock" in reg
    assert len(reg) == 1


def test_registry_duplicate_registration_rejected():
    reg = LockRegistry()
    reg.register(LockSpec(lock_id="l1", required_keys=("a",)))
    with pytest.raises(ValueError):
        reg.register(LockSpec(lock_id="l1", required_keys=("b",)))


def test_registry_unknown_lock_raises_keyerror():
    reg = LockRegistry()
    with pytest.raises(KeyError):
        reg.get("does_not_exist")


def test_registry_constructor_accepts_specs_iterable():
    specs = [
        LockSpec(lock_id="l1", required_keys=("a",)),
        LockSpec(lock_id="l2", required_keys=("b",)),
    ]
    reg = LockRegistry(specs)
    assert len(reg) == 2
    assert {s.lock_id for s in reg.all()} == {"l1", "l2"}
