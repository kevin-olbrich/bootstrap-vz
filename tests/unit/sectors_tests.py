import pytest
from bootstrapvz.common.sectors import Sectors
from bootstrapvz.common.bytes import Bytes
from bootstrapvz.common.exceptions import UnitError

std_secsz = Bytes(512)


def test_init_with_int():
    secsize = 4096
    assert Sectors('1MiB', secsize) == Sectors(256, secsize)


def test_lt():
    assert Sectors('1MiB', std_secsz) < Sectors('2MiB', std_secsz)


def test_le():
    assert Sectors('1MiB', std_secsz) <= Sectors('2MiB', std_secsz)
    assert Sectors('1MiB', std_secsz) <= Sectors('1MiB', std_secsz)


def test_eq():
    assert Sectors('1MiB', std_secsz) == Sectors('1MiB', std_secsz)


def test_neq():
    assert Sectors('15MiB', std_secsz) != Sectors('1MiB', std_secsz)


def test_gt():
    assert Sectors('2MiB', std_secsz) > Sectors('1MiB', std_secsz)


def test_ge():
    assert Sectors('2MiB', std_secsz) >= Sectors('1MiB', std_secsz)
    assert Sectors('2MiB', std_secsz) >= Sectors('2MiB', std_secsz)


def test_eq_unit():
    assert Sectors('1024MiB', std_secsz) == Sectors('1GiB', std_secsz)


def test_add():
    assert Sectors('2GiB', std_secsz) == Sectors('1GiB', std_secsz) + Sectors('1GiB', std_secsz)


def test_add_with_diff_secsize():
    with pytest.raises(UnitError):
        Sectors('1GiB', Bytes(512)) + Sectors('1GiB', Bytes(4096))


def test_iadd():
    s = Sectors('1GiB', std_secsz)
    s += Sectors('1GiB', std_secsz)
    assert Sectors('2GiB', std_secsz) == s


def test_sub():
    assert Sectors('1GiB', std_secsz) == Sectors('2GiB', std_secsz) - Sectors('1GiB', std_secsz)


def test_sub_int():
    secsize = Bytes('4KiB')
    assert Sectors('1MiB', secsize) == Sectors('1028KiB', secsize) - 1


def test_isub():
    s = Sectors('2GiB', std_secsz)
    s -= Sectors('1GiB', std_secsz)
    assert Sectors('1GiB', std_secsz) == s


def test_mul():
    assert Sectors('2GiB', std_secsz) == Sectors('1GiB', std_secsz) * 2


def test_mul_bytes():
    with pytest.raises(UnitError):
        Sectors('1GiB', std_secsz) * Sectors('1GiB', std_secsz)


def test_imul():
    s = Sectors('1GiB', std_secsz)
    s *= 2
    assert Sectors('2GiB', std_secsz) == s


def test_div():
    assert Sectors('1GiB', std_secsz) == Sectors('2GiB', std_secsz) / 2


def test_div_bytes():
    assert 2 == Sectors('2GiB', std_secsz) / Sectors('1GiB', std_secsz)


def test_idiv():
    s = Sectors('2GiB', std_secsz)
    s /= 2
    assert Sectors('1GiB', std_secsz) == s


def test_mod():
    assert Sectors('256MiB', std_secsz) == Sectors('1GiB', std_secsz) % Sectors('768MiB', std_secsz)


def test_mod_int():
    with pytest.raises(UnitError):
        Sectors('1GiB', std_secsz) % 768


def test_imod():
    s = Sectors('1GiB', std_secsz)
    s %= Sectors('768MiB', std_secsz)
    assert Sectors('256MiB', std_secsz) == s


def test_imod_int():
    with pytest.raises(UnitError):
        s = Sectors('1GiB', std_secsz)
        s %= 5


def test_convert_int():
    secsize = 512
    assert pow(1024, 3) / secsize == int(Sectors('1GiB', secsize))
