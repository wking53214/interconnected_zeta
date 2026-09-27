from zeta.state import LockState, LockStateStore


def test_get_creates_default_state_on_first_access():
    store = LockStateStore()
    state = store.get("patient1", "l1")
    assert state.open is False
    assert state.pending_since_count == 0
    assert state.locked_until is None


def test_get_returns_same_object_on_repeat_access():
    store = LockStateStore()
    s1 = store.get("patient1", "l1")
    s1.open = True
    s2 = store.get("patient1", "l1")
    assert s2.open is True


def test_set_overwrites_state():
    store = LockStateStore()
    store.set("patient1", "l1", LockState(open=True))
    assert store.get("patient1", "l1").open is True


def test_snapshot_returns_copy_of_entity_states():
    store = LockStateStore()
    store.set("patient1", "l1", LockState(open=True))
    store.set("patient1", "l2", LockState(open=False))

    snap = store.snapshot("patient1")

    assert set(snap.keys()) == {"l1", "l2"}
    assert snap["l1"].open is True


def test_snapshot_of_unknown_entity_is_empty():
    store = LockStateStore()
    assert store.snapshot("nobody") == {}


def test_restore_replaces_entity_states():
    store = LockStateStore()
    store.set("patient1", "l1", LockState(open=True))

    store.restore("patient1", {"l2": LockState(open=False)})

    snap = store.snapshot("patient1")
    assert set(snap.keys()) == {"l2"}


def test_states_isolated_across_entities():
    store = LockStateStore()
    store.get("patient1", "l1").open = True
    assert store.get("patient2", "l1").open is False


def test_snapshot_is_not_aliased_to_live_state():
    # Regression: snapshot() must return real copies. If LockState objects
    # stayed aliased to the live store, mutating the live store after
    # taking a snapshot would silently corrupt the snapshot too.
    store = LockStateStore()
    store.set("patient1", "l1", LockState(open=False, pending_since_count=0))

    snap = store.snapshot("patient1")
    assert snap["l1"].open is False

    live = store.get("patient1", "l1")
    live.open = True
    live.pending_since_count = 5

    assert snap["l1"].open is False, "snapshot must not change when live state mutates afterward"
    assert snap["l1"].pending_since_count == 0


def test_restore_does_not_alias_caller_supplied_states():
    store = LockStateStore()
    caller_state = LockState(open=True)
    store.restore("patient1", {"l1": caller_state})

    caller_state.open = False  # mutate the caller's own object after restoring

    assert store.get("patient1", "l1").open is True, "restore must copy, not alias, the supplied states"
