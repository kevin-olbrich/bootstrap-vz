import pytest
from bootstrapvz.common.bytes import Bytes
from bootstrapvz.common.exceptions import UnitError


def test_lt():
    assert Bytes('1MiB') < Bytes('2MiB')


def test_le():
    assert Bytes('1MiB') <= Bytes('2MiB')
    assert Bytes('1MiB') <= Bytes('1MiB')


def test_eq():
    assert Bytes('1MiB') == Bytes('1MiB')


def test_neq():
    assert Bytes('15MiB') != Bytes('1MiB')


def test_gt():
    assert Bytes('2MiB') > Bytes('1MiB')


def test_ge():
    assert Bytes('2MiB') >= Bytes('1MiB')
    assert Bytes('2MiB') >= Bytes('2MiB')


def test_eq_unit():
    assert Bytes('1024MiB') == Bytes('1GiB')


def test_add():
    assert Bytes('2GiB') == Bytes('1GiB') + Bytes('1GiB')


def test_iadd():
    b = Bytes('1GiB')
    b += Bytes('1GiB')
    assert Bytes('2GiB') == b


def test_sub():
    assert Bytes('1GiB') == Bytes('2GiB') - Bytes('1GiB')


def test_isub():
    b = Bytes('2GiB')
    b -= Bytes('1GiB')
    assert Bytes('1GiB') == b


def test_mul():
    assert Bytes('2GiB') == Bytes('1GiB') * 2


def test_mul_bytes():
    with pytest.raises(UnitError):
        Bytes('1GiB') * Bytes('1GiB')


def test_imul():
    b = Bytes('1GiB')
    b *= 2
    assert Bytes('2GiB') == b


def test_div():
    assert Bytes('1GiB') == Bytes('2GiB') / 2


def test_div_bytes():
    assert 2 == Bytes('2GiB') / Bytes('1GiB')


def test_idiv():
    b = Bytes('2GiB')
    b /= 2
    assert Bytes('1GiB') == b


def test_mod():
    assert Bytes('256MiB') == Bytes('1GiB') % Bytes('768MiB')


def test_mod_int():
    with pytest.raises(UnitError):
        Bytes('1GiB') % 768


def test_imod():
    b = Bytes('1GiB')
    b %= Bytes('768MiB')
    assert Bytes('256MiB') == b


def test_imod_int():
    with pytest.raises(UnitError):
        b = Bytes('1GiB')
        b %= 5


def test_convert_int():
    assert pow(1024, 3) == int(Bytes('1GiB'))
