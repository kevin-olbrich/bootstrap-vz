import os.path
import pytest
from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.base.fs import load_volume
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.common.tasks import filesystem
from bootstrapvz.common.tools import load_data

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/buster-console.yml')

MOUNTOPTS = ['defaults', 'noatime', 'errors=remount-ro']


def load_example_volume(partition_type):
    data = load_data(example)
    # grub cannot boot from unpartitioned volumes, extlinux works with every partition type
    data['system']['bootloader'] = 'extlinux'
    data['volume']['partitions']['type'] = partition_type
    data['volume']['partitions']['root']['mountopts'] = MOUNTOPTS
    manifest = Manifest(path=example, data=data)
    return load_volume(manifest.volume, manifest.system['bootloader'])


@pytest.mark.parametrize('partition_type', ['none', 'msdos', 'gpt'])
def test_root_partition_gets_mountopts(partition_type):
    assert load_example_volume(partition_type).partition_map.root.mountopts == MOUNTOPTS


def test_unpartitioned_root_mountopts_in_fstab(tmp_path, monkeypatch):
    monkeypatch.setattr('bootstrapvz.base.fs.partitions.abstract.log_check_call', lambda command: ['UUID'])
    (tmp_path / 'etc').mkdir()
    filesystem.FStab.run(DictClass(volume=load_example_volume('none'), root=str(tmp_path)))
    assert (tmp_path / 'etc/fstab').read_text() == 'UUID=UUID / ext4 defaults,noatime,errors=remount-ro 1 1\n'
