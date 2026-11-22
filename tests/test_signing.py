"""The lock-result signing slot: zeta builds the bytes to sign and calls what it is handed; it never holds a key.

The operator's signer here is an HMAC-SHA256 over a test key (stdlib only), standing in for Ed25519 or a KMS. The rules
are held to interconnected_delta's ``delta.signing`` byte for byte: the same domain label shape, the same canonical
ASCII JSON, and the same strict UTF-8 check (whose source is pinned below as DELTA's).
"""
import dataclasses
import hashlib
import hmac
import inspect
import json
import re
from datetime import datetime, timedelta

import pytest

import zeta
from zeta import Combination, Key, KeySet, LockEvaluator, LockRegistry, LockResult, LockSpec
from zeta import signing
from zeta.signing import (
    LOCK_RESULT_SIGNATURE_DOMAIN,
    lock_result_signing_input,
    sign_lock_result,
    strict_utf8,
    verify_lock_result_signature,
)

T0 = datetime(2026, 1, 1, 12, 0, 0)

# delta.signing.strict_utf8 at interconnected_delta b85e46c, verbatim. zeta's copy must match it character for character.
DELTA_STRICT_UTF8_SOURCE = '''def strict_utf8(name: str, value: str) -> None:
    """``ValueError`` naming ``name`` unless ``value`` encodes as strict UTF-8 (so no lone or paired surrogates).

    ``ensure_ascii`` JSON writes an astral character and the surrogate pair of two code points the same way, so
    without this two different strings would share one signing input."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{name} does not encode as strict UTF-8 (a surrogate code point is not a character)") from None
'''
DELTA_RECEIPT_DOMAIN = b"delta-ledger-receipt-signature/1\n"
DOMAIN_SHAPE = re.compile(rb"^[a-z]+-[a-z]+(-[a-z]+)*-signature/[0-9]+\n$")


def hmac_pair(key=b"operator-test-key"):
    def sign(data):
        return hmac.new(key, data, hashlib.sha256).digest()

    def verify(data, signature):
        return hmac.compare_digest(hmac.new(key, data, hashlib.sha256).digest(), signature)

    return sign, verify


def registry():
    return LockRegistry([
        LockSpec(lock_id="hazard", required_keys=("a", "b"), combination=Combination.OR, force=True, lock_seconds=60),
        LockSpec(lock_id="clear", required_keys=("a", "b"), combination=Combination.AND, dwell_threshold=2),
    ])


def keys(a=True, b=False):
    return KeySet([Key(name="a", present=a, reason="ra"), Key(name="b", present=b, reason="rb")])


def a_result(**overrides):
    base = dict(lock_id="hazard", open=True, changed=True, forced=False, reasons=["r"], stale=False,
                keyset_fingerprint="f" * 64, entity_id="p1")
    base.update(overrides)
    return LockResult(**base)


# The rules, against DELTA's.

def test_the_slot_is_exported_from_the_package():
    for name in ("LOCK_RESULT_SIGNATURE_DOMAIN", "lock_result_signing_input", "sign_lock_result",
                 "verify_lock_result_signature"):
        assert name in zeta.__all__ and getattr(zeta, name) is getattr(signing, name)


def test_strict_utf8_is_delta_s_copy_character_for_character():
    assert inspect.getsource(strict_utf8) == DELTA_STRICT_UTF8_SOURCE


def test_the_domain_label_has_delta_s_shape():
    assert LOCK_RESULT_SIGNATURE_DOMAIN == b"zeta-lock-result-signature/1\n"
    assert DOMAIN_SHAPE.match(LOCK_RESULT_SIGNATURE_DOMAIN)
    assert DOMAIN_SHAPE.match(DELTA_RECEIPT_DOMAIN)
    assert LOCK_RESULT_SIGNATURE_DOMAIN != DELTA_RECEIPT_DOMAIN


def test_the_body_is_delta_s_canonical_ascii_json():
    result = a_result(reasons=["caf\u00e9", "\U0001f600"])
    data = lock_result_signing_input(result)
    assert data.startswith(LOCK_RESULT_SIGNATURE_DOMAIN)
    body = data[len(LOCK_RESULT_SIGNATURE_DOMAIN):]
    expected = {"schema": "zeta-lock-result/1", "lock_id": "hazard", "open": True, "changed": True, "forced": False,
                "reasons": ["caf\u00e9", "\U0001f600"], "stale": False, "keyset_fingerprint": "f" * 64,
                "entity_id": "p1"}
    assert body == json.dumps(expected, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                              allow_nan=False).encode("ascii")
    data.decode("ascii")


def test_a_fixed_vector_comes_out_byte_for_byte():
    result = LockResult(lock_id="l", open=True, changed=False, forced=False, reasons=["r \u00e9"], stale=False,
                        keyset_fingerprint="ab", entity_id="p1")
    assert lock_result_signing_input(result) == (
        b"zeta-lock-result-signature/1\n"
        b'{"changed":false,"entity_id":"p1","forced":false,"keyset_fingerprint":"ab","lock_id":"l","open":true,'
        b'"reasons":["r \\u00e9"],"schema":"zeta-lock-result/1","stale":false}'
    )


def test_a_surrogate_is_refused_so_it_cannot_collide_with_the_character_it_spells():
    with pytest.raises(ValueError, match="strict UTF-8"):
        lock_result_signing_input(a_result(reasons=["\ud83d\ude00"]))
    with pytest.raises(ValueError, match="entity_id"):
        lock_result_signing_input(a_result(entity_id="\ud800"))
    assert lock_result_signing_input(a_result(reasons=["\U0001f600"]))


def test_the_signature_field_is_not_part_of_what_is_signed():
    assert lock_result_signing_input(a_result()) == lock_result_signing_input(a_result(signature=b"anything"))


# What can be signed.

def test_a_subclass_is_refused():
    class Sneaky(LockResult):
        pass

    with pytest.raises(TypeError, match="exact LockResult"):
        lock_result_signing_input(Sneaky(**dataclasses.asdict(a_result())))


@pytest.mark.parametrize("field,value", [("open", 1), ("changed", 0), ("forced", None), ("stale", "no")])
def test_a_flag_that_is_not_an_exact_bool_is_refused(field, value):
    with pytest.raises(TypeError, match=field):
        lock_result_signing_input(a_result(**{field: value}))


def test_reasons_must_be_an_exact_list_of_exact_str():
    with pytest.raises(TypeError, match="reasons"):
        lock_result_signing_input(a_result(reasons=("r",)))
    with pytest.raises(TypeError, match=r"reasons\[1\]"):
        lock_result_signing_input(a_result(reasons=["r", 3]))


@pytest.mark.parametrize("field", ("lock_id", "keyset_fingerprint", "entity_id"))
def test_a_result_that_names_no_lock_entity_or_keyset_cannot_be_signed(field):
    with pytest.raises(ValueError, match=field):
        lock_result_signing_input(a_result(**{field: ""}))
    with pytest.raises(TypeError, match=field):
        lock_result_signing_input(a_result(**{field: 7}))


def test_a_hand_built_default_result_cannot_be_signed():
    with pytest.raises(ValueError):
        lock_result_signing_input(LockResult(lock_id="x", open=True, changed=False, forced=False))


# Sign and verify.

def test_round_trip():
    sign, verify = hmac_pair()
    result = a_result()
    signature = sign_lock_result(result, sign)
    assert type(signature) is bytes and len(signature) == 32
    assert verify_lock_result_signature(result, signature, verify) is True


@pytest.mark.parametrize("field,value", [
    ("lock_id", "clear"), ("open", False), ("changed", False), ("forced", True), ("reasons", ["s"]),
    ("reasons", ["r", "r"]), ("reasons", []), ("stale", True), ("keyset_fingerprint", "e" * 64), ("entity_id", "p2"),
])
def test_a_one_field_change_invalidates_the_signature(field, value):
    sign, verify = hmac_pair()
    signature = sign_lock_result(a_result(), sign)
    assert verify_lock_result_signature(a_result(**{field: value}), signature, verify) is False


def test_the_wrong_key_does_not_verify():
    sign, _ = hmac_pair(b"k1")
    _, verify = hmac_pair(b"k2")
    assert verify_lock_result_signature(a_result(), sign_lock_result(a_result(), sign), verify) is False


def test_a_missing_truncated_or_wrong_typed_signature_is_false():
    sign, verify = hmac_pair()
    signature = sign_lock_result(a_result(), sign)
    for bad in (b"", signature[:-1], signature.hex(), bytearray(signature), None):
        assert verify_lock_result_signature(a_result(), bad, verify) is False


def test_a_verifier_that_raises_or_answers_truthy_is_false():
    def raises(data, signature):
        raise RuntimeError("boom")

    assert verify_lock_result_signature(a_result(), b"x", raises) is False
    assert verify_lock_result_signature(a_result(), b"x", lambda d, s: 1) is False
    assert verify_lock_result_signature(a_result(), b"x", lambda d, s: "yes") is False


def test_an_unsignable_result_does_not_verify():
    assert verify_lock_result_signature(a_result(entity_id=""), b"x", lambda d, s: True) is False


@pytest.mark.parametrize("bad", ["sig", b"", bytearray(b"sig")])
def test_a_signer_that_returns_anything_but_non_empty_bytes_is_refused(bad):
    with pytest.raises((TypeError, ValueError)):
        sign_lock_result(a_result(), lambda data: bad)


def test_sign_and_verify_must_be_callable():
    with pytest.raises(TypeError):
        sign_lock_result(a_result(), b"key")
    with pytest.raises(TypeError):
        verify_lock_result_signature(a_result(), b"x", None)


# The evaluator's slot.

def run_every_path(evaluator):
    """Forced open, cooldown, closing through dwell, pending, already closed, and stale."""
    out = [evaluator.evaluate("p1", keys(a=True), "hazard", T0)]
    out.append(evaluator.evaluate("p1", keys(a=False), "hazard", T0 + timedelta(seconds=10)))
    out.append(evaluator.evaluate("p1", keys(a=False), "hazard", T0 + timedelta(seconds=100)))
    out.append(evaluator.evaluate("p1", keys(a=True, b=True), "clear", T0))
    out.append(evaluator.evaluate("p1", keys(a=True, b=True), "clear", T0 + timedelta(seconds=1)))
    out.append(evaluator.evaluate("p1", keys(a=False), "clear", T0 + timedelta(seconds=2)))
    out.append(evaluator.evaluate("p1", keys(a=False), "clear", T0))
    out.extend(evaluator.evaluate_all("p2", keys(), T0).values())
    return out


def test_with_no_signer_nothing_changes_and_nothing_is_signed():
    evaluator = LockEvaluator(registry())
    assert evaluator.signer is None
    results = run_every_path(evaluator)
    assert any(r.stale for r in results) and any(r.forced for r in results)
    assert all(r.signature == b"" for r in results)


def test_with_a_signer_every_path_is_signed_and_otherwise_identical():
    sign, verify = hmac_pair()
    plain = run_every_path(LockEvaluator(registry()))
    signed = run_every_path(LockEvaluator(registry(), signer=sign))
    assert len(plain) == len(signed)
    for p, s in zip(plain, signed):
        assert s.signature and verify_lock_result_signature(s, s.signature, verify) is True
        assert dataclasses.replace(s, signature=b"") == p


def test_a_signer_must_be_callable():
    with pytest.raises(TypeError, match="signer"):
        LockEvaluator(registry(), signer=b"key")


@pytest.mark.parametrize("bad_signer", [lambda d: "str", lambda d: b"", lambda d: 1 / 0])
def test_a_failing_signer_puts_the_lock_states_back(bad_signer):
    evaluator = LockEvaluator(registry(), signer=bad_signer)
    before = evaluator.state_store.snapshot("p1")
    with pytest.raises(Exception):
        evaluator.evaluate("p1", keys(a=True), "hazard", T0)
    with pytest.raises(Exception):
        evaluator.evaluate_all("p1", keys(a=True), T0)
    assert evaluator.state_store.snapshot("p1") == before


def test_a_failing_signer_lets_a_retry_count_once():
    calls = {"n": 0}
    sign, verify = hmac_pair()

    def flaky(data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("signer offline")
        return sign(data)

    evaluator = LockEvaluator(registry(), signer=flaky)
    with pytest.raises(RuntimeError):
        evaluator.evaluate_all("p1", keys(a=True, b=True), T0)
    results = evaluator.evaluate_all("p1", keys(a=True, b=True), T0)
    assert results["clear"].open is False and "1/2" in results["clear"].reasons[0]
    assert all(verify_lock_result_signature(r, r.signature, verify) for r in results.values())
