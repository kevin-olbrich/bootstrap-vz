import os.path
import pytest
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.common.exceptions import ManifestError
from bootstrapvz.common.tools import load_data

examples = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests/examples/kvm')
partitioned = os.path.join(examples, 'buster-cloudimg.yml')
lvm = os.path.join(examples, 'jessie-lvm.yml')


def validation_error(path, change):
    data = load_data(path)
    change(data)
    with pytest.raises(ManifestError) as excinfo:
        Manifest(path=path, data=data)
    return excinfo.value.message, list(excinfo.value.data_path)


def test_invalid_partition_setting_names_its_data_path():
    def change(data):
        data['volume']['partitions']['root']['filesystem'] = 'ext5'
    assert validation_error(partitioned, change) == (
        "'ext5' is not one of ['ext2', 'ext3', 'ext4', 'xfs', 'btrfs']",
        ['volume', 'partitions', 'root', 'filesystem'])


def test_misspelled_partition_key_is_reported():
    def change(data):
        data['volume']['partitions']['root']['mountopt'] = ['noatime']
    assert validation_error(partitioned, change) == (
        "Additional properties are not allowed ('mountopt' was unexpected)",
        ['volume', 'partitions', 'root'])


def test_error_inside_anyof_names_its_absolute_data_path():
    def change(data):
        data['packages']['install'].append('a/b/c')
    message, data_path = validation_error(partitioned, change)
    assert "'a/b/c'" in message
    assert data_path == ['packages', 'install', 3]


@pytest.mark.parametrize('key', ['volumegroup', 'logicalvolume'])
def test_lvm_backing_requires_volumegroup_and_logicalvolume(key):
    def change(data):
        del data['volume'][key]
    assert validation_error(lvm, change) == ('The lvm backing requires ' + key, ['volume'])


def test_volumegroup_needs_lvm_backing():
    def change(data):
        data['volume']['backing'] = 'raw'
    assert validation_error(lvm, change) == ('volumegroup can only be used with the lvm backing',
                                             ['volume', 'volumegroup'])


def test_unpartitioned_volume_only_has_a_root_partition():
    def change(data):
        data['system']['bootloader'] = 'extlinux'
        data['volume']['partitions']['type'] = 'none'
        data['volume']['partitions']['boot'] = {'filesystem': 'ext2', 'size': '64MiB'}
    assert validation_error(partitioned, change) == ('Unpartitioned volumes can only have a root partition',
                                                     ['volume', 'partitions', 'boot'])


def test_unpartitioned_volume_is_valid():
    data = load_data(partitioned)
    data['system']['bootloader'] = 'extlinux'
    data['volume']['partitions']['type'] = 'none'
    Manifest(path=partitioned, data=data)


def test_unknown_release_is_a_manifest_error():
    def change(data):
        data['system']['release'] = 'bookwrom'
    assert validation_error(partitioned, change) == ("The release `bookwrom' is unknown", ['system', 'release'])
