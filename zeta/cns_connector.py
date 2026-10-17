"""Optional connector to CNS (``cns.gate``): binding only.

zeta is independent. It has no runtime dependency, imports nothing from CNS
when it loads, and its whole suite passes with CNS absent. This module is the
one place that knows CNS exists, and it asks for CNS only when a digest is
computed. Without CNS those calls raise :class:`CnsNotInstalled` with the
install command; nothing else in zeta is affected. The ``*_content`` functions
are pure and need no CNS at all.

What connecting means
---------------------
CNS's shared gate contract is ``cns.gate.GateResult``, whose ``subject`` and
``subject_digest`` record what a verdict was issued against, and whose
``binds(subject, digest)`` says whether it was issued against exactly some
content. This module makes zeta's outputs *bindable*: given an output, it
produces the deterministic content and digest a CNS verdict about that output
carries, and :func:`binds` checks a verdict against the output.

It is deliberately NOT a gate. zeta says an open lock is a boolean interlock,
not an authorization, so this module offers no gate, no PASS / RETRY / BREACH
mapping and no chain. Whoever judges a lock result owns the verdict; zeta only
lets that verdict be checked against the result it was about.

What is bound
-------------
* ``LockResult``: ``lock_id``, ``open``, ``changed``, ``forced`` and
  ``reasons`` (in the order the evaluator wrote them, which is part of the
  record). Subject label :data:`LOCK_RESULT_SUBJECT`.
* The snapshot ``LockStateStore.snapshot(entity_id)`` returns, a mapping of
  ``lock_id`` to ``LockState``: every field of every state. Locks are keyed by
  id, so the order of the mapping cannot change the digest. Datetimes are
  rendered with ``isoformat()``, as the evaluator itself does in its reasons.
  Subject label :data:`LOCK_SNAPSHOT_SUBJECT`.

A ``LockResult`` and a snapshot do not record which entity they are about
(``evaluate`` takes an ``entity_id`` but does not keep it). Pass ``entity_id``
to bind it too; without it, two entities with identical results bind alike.

Install with the extra:
``pip install 'zeta[cns] @ git+https://github.com/wking53214/interconnected_zeta'``.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from datetime import datetime
from types import ModuleType
from typing import Any, Dict, Optional

from .evaluator import LockResult
from .state import LockState

__all__ = [
    "CnsNotInstalled",
    "LOCK_RESULT_SUBJECT",
    "LOCK_SNAPSHOT_SUBJECT",
    "binds",
    "cns_available",
    "lock_result_content",
    "lock_result_digest",
    "snapshot_content",
    "snapshot_digest",
]

INSTALL_HINT = "pip install 'zeta[cns] @ git+https://github.com/wking53214/interconnected_zeta'"

LOCK_RESULT_SUBJECT = "zeta.lock_result"
LOCK_SNAPSHOT_SUBJECT = "zeta.lock_snapshot"


class CnsNotInstalled(ImportError):
    """Raised by this module's digest functions when ``cns`` cannot be imported."""


def _cns_gate() -> ModuleType:
    """Import ``cns.gate`` on demand, or say exactly what is missing."""
    try:
        return importlib.import_module("cns.gate")
    except ImportError as exc:
        raise CnsNotInstalled(
            "zeta.cns_connector needs the CNS package (cns.gate), which is not "
            f"installed. Install it with: {INSTALL_HINT}. zeta itself works "
            "without it."
        ) from exc


def cns_available() -> bool:
    """Whether the CNS gate contract can be imported in this environment."""
    try:
        _cns_gate()
    except CnsNotInstalled:
        return False
    return True


def _text(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{what} must be a str to be bound; got {type(value).__name__}")
    return value


def _entity(entity_id: Optional[str]) -> Optional[str]:
    return None if entity_id is None else _text(entity_id, "entity_id")


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"LockState.pending_since_count must be an int to be bound; got {type(value).__name__}")
    return value


def _moment(value: Optional[datetime], what: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"{what} must be a datetime or None to be bound; got {type(value).__name__}")
    return value.isoformat()


def lock_result_content(result: LockResult, *, entity_id: Optional[str] = None) -> Dict[str, Any]:
    """The deterministic content a verdict on ``result`` is bound to."""
    if not isinstance(result, LockResult):
        raise TypeError(f"expected a LockResult; got {type(result).__name__}")
    if not isinstance(result.reasons, (list, tuple)):
        raise TypeError(f"LockResult.reasons must be a list; got {type(result.reasons).__name__}")
    return {
        "entity_id": _entity(entity_id),
        "lock_id": _text(result.lock_id, "LockResult.lock_id"),
        "open": bool(result.open),
        "changed": bool(result.changed),
        "forced": bool(result.forced),
        "reasons": [_text(r, "a LockResult reason") for r in result.reasons],
    }


def _state_content(state: LockState) -> Dict[str, Any]:
    if not isinstance(state, LockState):
        raise TypeError(f"a snapshot holds LockState values; got {type(state).__name__}")
    return {
        "open": bool(state.open),
        "pending_open": None if state.pending_open is None else bool(state.pending_open),
        "pending_since_count": _count(state.pending_since_count),
        "locked_until": _moment(state.locked_until, "LockState.locked_until"),
        "opened_at": _moment(state.opened_at, "LockState.opened_at"),
        "forced": bool(state.forced),
    }


def snapshot_content(snapshot: Mapping, *, entity_id: Optional[str] = None) -> Dict[str, Any]:
    """The deterministic content a verdict on a ``LockStateStore.snapshot`` is bound to."""
    if not isinstance(snapshot, Mapping):
        raise TypeError(f"expected a mapping of lock_id to LockState; got {type(snapshot).__name__}")
    locks = {_text(lock_id, "a snapshot key"): _state_content(state) for lock_id, state in snapshot.items()}
    return {"entity_id": _entity(entity_id), "locks": locks}


def lock_result_digest(result: LockResult, *, entity_id: Optional[str] = None) -> str:
    """The digest a verdict bound to ``result`` carries."""
    return _cns_gate().subject_digest(lock_result_content(result, entity_id=entity_id))


def snapshot_digest(snapshot: Mapping, *, entity_id: Optional[str] = None) -> str:
    """The digest a verdict bound to ``snapshot`` carries."""
    return _cns_gate().subject_digest(snapshot_content(snapshot, entity_id=entity_id))


def binds(result: Any, obj: object, *, subject: Optional[str] = None, entity_id: Optional[str] = None) -> bool:
    """Whether the ``cns.gate.GateResult`` ``result`` was issued against exactly ``obj``.

    ``obj`` is a ``LockResult`` or a snapshot mapping. ``subject`` defaults to
    that type's label. An unbound or hand-built result is ``False``. This
    checks what a verdict was about; it says nothing about what the verdict
    was.
    """
    if isinstance(obj, LockResult):
        default, digest = LOCK_RESULT_SUBJECT, lock_result_digest(obj, entity_id=entity_id)
    elif isinstance(obj, Mapping):
        default, digest = LOCK_SNAPSHOT_SUBJECT, snapshot_digest(obj, entity_id=entity_id)
    else:
        raise TypeError(f"expected a LockResult or a snapshot mapping; got {type(obj).__name__}")
    return bool(result.binds(default if subject is None else subject, digest))
