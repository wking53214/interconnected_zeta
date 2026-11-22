"""A signing slot for lock results. zeta builds the bytes to sign and calls what it is handed; it holds no key.

``sign`` and ``verify`` are callables the operator supplies (an Ed25519 private key's ``sign``, a KMS wrapper, a
verifier bound to a public key). Key custody, trust, rotation and revocation belong to those callables, not to zeta.
This module imports nothing from CNS and nothing cryptographic.

The signing rules are a byte-for-byte copy of interconnected_delta's ``delta.signing`` (the receipt hook): a domain
label of the form ``<repo>-<object>-signature/<version>`` plus a newline, then the object as canonical ASCII JSON
(sorted keys, no spaces, ``ensure_ascii``, no NaN), with every string checked as strict UTF-8 first.
``tests/test_signing.py`` holds this copy to those rules.

Limit, said plainly: the key lives with whatever code runs the evaluator. Code in the same process that can call the
signer can sign a lock result it made up. A signature proves the holder of the signing callable produced these exact
fields; it does not prove the evaluation was honest. A separate signer process is the next step.
"""
import json
from collections.abc import Callable
from typing import Any

from .evaluator import LockResult

LOCK_RESULT_SIGNATURE_DOMAIN = b"zeta-lock-result-signature/1\n"
LOCK_RESULT_SCHEMA = "zeta-lock-result/1"

_STR_FIELDS = ("lock_id", "keyset_fingerprint", "entity_id")
_BOOL_FIELDS = ("open", "changed", "forced", "stale")


def strict_utf8(name: str, value: str) -> None:
    """``ValueError`` naming ``name`` unless ``value`` encodes as strict UTF-8 (so no lone or paired surrogates).

    ``ensure_ascii`` JSON writes an astral character and the surrogate pair of two code points the same way, so
    without this two different strings would share one signing input."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{name} does not encode as strict UTF-8 (a surrogate code point is not a character)") from None


def lock_result_signing_input(result: LockResult) -> bytes:
    """The exact bytes a lock result is signed over: the domain label, then its fields as canonical ASCII JSON.

    Covers ``schema`` plus every LockResult field except ``signature``: lock_id, open, changed, forced, reasons,
    stale, keyset_fingerprint and entity_id. The result must be an exact ``LockResult`` (no subclass); each field must
    be its exact type (``str``; ``bool``; ``reasons`` an exact ``list`` of exact ``str``). ``lock_id``, ``entity_id``
    and ``keyset_fingerprint`` must be non-empty, so a hand-built result that names no entity and no KeySet cannot be
    signed and then replayed for any subject. Anything else is a ``TypeError`` or ``ValueError`` naming the field.
    """
    if type(result) is not LockResult:
        raise TypeError(f"a lock result is an exact LockResult; got {type(result).__name__}")
    body: dict = {"schema": LOCK_RESULT_SCHEMA}
    for name in _STR_FIELDS:
        value = getattr(result, name)
        if type(value) is not str:
            raise TypeError(f"lock result field {name} must be an exact str; got {type(value).__name__}")
        if not value:
            raise ValueError(f"lock result field {name} must be non-empty to be signed")
        strict_utf8(f"lock result field {name}", value)
        body[name] = value
    for name in _BOOL_FIELDS:
        value = getattr(result, name)
        if type(value) is not bool:
            raise TypeError(f"lock result field {name} must be an exact bool; got {type(value).__name__}")
        body[name] = value
    reasons = result.reasons
    if type(reasons) is not list:
        raise TypeError(f"lock result field reasons must be an exact list; got {type(reasons).__name__}")
    for i, reason in enumerate(reasons):
        if type(reason) is not str:
            raise TypeError(f"lock result field reasons[{i}] must be an exact str; got {type(reason).__name__}")
        strict_utf8(f"lock result field reasons[{i}]", reason)
    body["reasons"] = list(reasons)
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return LOCK_RESULT_SIGNATURE_DOMAIN + text.encode("ascii")


def sign_lock_result(result: LockResult, sign: Callable[[bytes], bytes]) -> bytes:
    """Sign a lock result with the operator's ``sign`` callable (bytes in, bytes out). The result must be exactly a
    non-empty ``bytes``; a ``str``, a subclass or empty bytes is refused. zeta never sees a key."""
    if not callable(sign):
        raise TypeError(f"sign must be callable; got {type(sign).__name__}")
    signature: Any = sign(lock_result_signing_input(result))
    if type(signature) is not bytes:
        raise TypeError(f"the signer must return exactly bytes; got {type(signature).__name__}")
    if not signature:
        raise ValueError("the signer returned an empty signature")
    return signature


def verify_lock_result_signature(result: LockResult, signature: bytes, verify: Callable[[bytes, bytes], bool]) -> bool:
    """Whether ``verify(signing_input, signature)`` answers exactly ``True``. Any other answer, any exception from
    ``verify``, a signature that is not exactly non-empty ``bytes``, or a result that cannot be signed, is ``False``.

    ``signature`` is passed separately (usually ``result.signature``) so the caller says which signature it checks."""
    if not callable(verify):
        raise TypeError(f"verify must be callable; got {type(verify).__name__}")
    if type(signature) is not bytes or not signature:
        return False
    try:
        data = lock_result_signing_input(result)
    except (TypeError, ValueError):
        return False
    try:
        return verify(data, signature) is True
    except Exception:
        return False
