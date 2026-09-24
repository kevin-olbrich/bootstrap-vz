import pytest
from bootstrapvz.common import releases


def test_gt():
    assert releases.wheezy > releases.squeeze


def test_lt():
    assert releases.wheezy < releases.stretch


def test_eq():
    assert releases.wheezy == releases.wheezy


def test_neq():
    assert releases.wheezy != releases.jessie


def test_identity():
    assert releases.wheezy is releases.wheezy


def test_not_identity():
    # "==" tests equality "is" tests identity
    assert releases.trixie == releases.stable
    assert releases.trixie is not releases.stable

    assert releases.stable is releases.stable
    assert releases.trixie is releases.trixie

    assert releases.bookworm != releases.stable
    assert releases.bookworm is not releases.stable


def test_alias():
    assert releases.oldstable == releases.bookworm
    assert releases.stable == releases.trixie
    assert releases.testing == releases.forky
    assert releases.unstable == releases.sid


def test_future_release():
    assert releases.duke > releases.forky
    assert releases.get_release('duke') is releases.duke


def test_sid_is_newest():
    assert releases.sid > releases.duke


def test_bogus_releasename():
    with pytest.raises(releases.UnknownReleaseException):
        releases.get_release('nemo')
