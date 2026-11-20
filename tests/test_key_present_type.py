import pytest

from zeta.keys import Key


@pytest.mark.parametrize("bad", ["no", "false", 0, 1, None])
def test_present_must_be_a_real_bool(bad):
    with pytest.raises(TypeError):
        Key(name="k", present=bad)


@pytest.mark.parametrize("ok", [True, False])
def test_present_accepts_true_and_false(ok):
    assert Key(name="k", present=ok).present is ok
