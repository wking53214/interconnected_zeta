import pytest

from zeta import Key, KeySet


def test_key_valid_confidence():
    k = Key(name="fever", present=True, confidence=0.9, reason="temp 103F")
    assert k.name == "fever"
    assert k.present is True
    assert k.confidence == 0.9


@pytest.mark.parametrize("confidence", [-0.1, 1.1, 2.0, -5.0])
def test_key_rejects_out_of_range_confidence(confidence):
    with pytest.raises(ValueError):
        Key(name="fever", present=True, confidence=confidence)


def test_keyset_is_present_true_when_present_and_confident():
    ks = KeySet([Key(name="fever", present=True, confidence=0.9)])
    assert ks.is_present("fever") is True


def test_keyset_is_present_false_when_not_present():
    ks = KeySet([Key(name="fever", present=False, confidence=0.9)])
    assert ks.is_present("fever") is False


def test_keyset_is_present_false_when_unknown_key():
    ks = KeySet([Key(name="fever", present=True)])
    assert ks.is_present("tachycardia") is False


def test_keyset_min_confidence_filters_low_confidence_keys():
    ks = KeySet([Key(name="fever", present=True, confidence=0.4)], min_confidence=0.5)
    assert ks.is_present("fever") is False


def test_keyset_min_confidence_allows_at_threshold():
    ks = KeySet([Key(name="fever", present=True, confidence=0.5)], min_confidence=0.5)
    assert ks.is_present("fever") is True


def test_keyset_names_present():
    ks = KeySet([
        Key(name="fever", present=True),
        Key(name="tachycardia", present=False),
        Key(name="tachypnea", present=True),
    ])
    assert ks.names_present() == {"fever", "tachypnea"}


def test_keyset_rejects_invalid_min_confidence():
    with pytest.raises(ValueError):
        KeySet([], min_confidence=1.5)


def test_all_keys_lists_every_key_including_absent_and_low_confidence():
    fever = Key(name="fever", present=True, confidence=0.9)
    absent = Key(name="tachycardia", present=False)
    weak = Key(name="weak", present=True, confidence=0.3)
    ks = KeySet([fever, absent, weak], min_confidence=0.5)
    assert ks.names_present() == {"fever"}
    assert ks.all_keys() == (fever, absent, weak)


def test_all_keys_keeps_the_last_key_for_a_repeated_name_and_cannot_edit_the_set():
    first = Key(name="fever", present=False)
    second = Key(name="fever", present=True)
    ks = KeySet([first, second])
    keys = ks.all_keys()
    assert keys == (second,)
    assert isinstance(keys, tuple) and len(ks) == 1


def test_all_keys_of_an_empty_set_is_empty():
    assert KeySet([]).all_keys() == ()
