import os.path
import re
from itertools import chain

import pytest

from bootstrapvz.base.fs import load_volume
from bootstrapvz.common.bytes import Bytes
from bootstrapvz.common.tools import load_data

from .. import recursive_glob

# Sizes in sectors of 512 bytes, the sector size that bootstrap-vz partitions with
MiB = 2048
GiB = 1024 * MiB

LAYOUTS = {
    'root': {'root': {'filesystem': 'ext4', 'size': '8GiB'}},
    'boot+swap+root': {'boot': {'filesystem': 'ext2', 'size': '64MiB'},
                       'swap': {'size': '128MiB'},
                       'root': {'filesystem': 'ext4', 'size': '8GiB'}},
}

# First and last sector (parted includes both) of each partition, in the order they are created
EXPECTED_LAYOUTS = {
    # grub embeds itself in the 2MiB before the first partition
    ('msdos', 'grub', 'root'): [(2 * MiB, 8 * GiB - 1)],
    ('msdos', 'grub', 'boot+swap+root'): [(2 * MiB, 64 * MiB - 1), (64 * MiB, 192 * MiB - 1),
                                          (192 * MiB, 8 * GiB + 192 * MiB - 1)],
    ('msdos', 'extlinux', 'root'): [(MiB, 8 * GiB - 1)],
    ('msdos', 'extlinux', 'boot+swap+root'): [(MiB, 64 * MiB - 1), (64 * MiB, 192 * MiB - 1),
                                              (192 * MiB, 8 * GiB + 192 * MiB - 1)],
    # The bios_grub partition fills the space between the primary GPT (34 sectors) and 1MiB,
    # the last 34 sectors are left for the secondary GPT
    ('gpt', 'grub', 'root'): [(34, MiB - 1), (MiB, 8 * GiB - 35)],
    ('gpt', 'grub', 'boot+swap+root'): [(34, MiB - 1), (MiB, 64 * MiB - 1), (64 * MiB, 192 * MiB - 1),
                                        (192 * MiB, 8 * GiB + 192 * MiB - 35)],
    ('gpt', 'extlinux', 'root'): [(MiB, 8 * GiB - 35)],
    ('gpt', 'extlinux', 'boot+swap+root'): [(MiB, 64 * MiB - 1), (64 * MiB, 192 * MiB - 1),
                                            (192 * MiB, 8 * GiB + 192 * MiB - 35)],
}


def example_volume(partition_type, layout):
    return {'backing': 'raw', 'partitions': dict(LAYOUTS[layout], type=partition_type)}


def volumes():
    """The example layouts and the volume of every manifest in manifests/ that has a partition table"""
    for partition_type, bootloader, layout in EXPECTED_LAYOUTS:
        yield pytest.param(example_volume(partition_type, layout), bootloader,
                           id='-'.join([partition_type, bootloader, layout]))
    manifests = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests')
    for path in sorted(chain(recursive_glob(manifests, '*.yml'), recursive_glob(manifests, '*.yaml'))):
        data = load_data(path)
        if data['volume']['partitions']['type'] != 'none':
            yield pytest.param(data['volume'], data['system']['bootloader'],
                               id=os.path.relpath(path, manifests))


VOLUMES = list(volumes())


def create_partitions(monkeypatch, tmp_path, volume_data, bootloader):
    """Creates the partition map of the volume and returns the volume and the first and last sector
    of each partition that parted is asked to create. Nothing is run on the host.
    """
    commands = []

    def log_call(command, *args, **kwargs):
        commands.append(command)
        return 0, [], []
    monkeypatch.setattr('bootstrapvz.common.tools.log_call', log_call)
    volume = load_volume(volume_data, bootloader)
    volume.device_path = str(tmp_path / 'volume.raw')
    volume.partition_map.create(volume)
    mkpart = re.compile(r'^mkpart primary [\w-]+ (?P<start>\d+)s (?P<end>\d+)s$')
    matches = (mkpart.match(command[-1]) for command in commands)
    ranges = [(int(match.group('start')), int(match.group('end'))) for match in matches if match]
    assert len(ranges) == len(volume.partition_map.partitions)
    return volume, ranges


@pytest.mark.parametrize('partition_type,bootloader,layout', EXPECTED_LAYOUTS.keys(),
                         ids=['-'.join(key) for key in EXPECTED_LAYOUTS])
def test_partition_layout(monkeypatch, tmp_path, partition_type, bootloader, layout):
    _, ranges = create_partitions(monkeypatch, tmp_path, example_volume(partition_type, layout), bootloader)
    assert ranges == EXPECTED_LAYOUTS[(partition_type, bootloader, layout)]


@pytest.mark.parametrize('volume_data,bootloader', VOLUMES)
def test_partitions_start_on_mib_boundaries(monkeypatch, tmp_path, volume_data, bootloader):
    volume, ranges = create_partitions(monkeypatch, tmp_path, volume_data, bootloader)
    for partition, (start, _) in zip(volume.partition_map.partitions, ranges):
        # The bios_grub partition uses the space between the primary GPT and the first MiB
        if 'bios_grub' not in partition.flags:
            assert start % MiB == 0


@pytest.mark.parametrize('volume_data,bootloader', VOLUMES)
def test_partitions_do_not_overlap(monkeypatch, tmp_path, volume_data, bootloader):
    volume, ranges = create_partitions(monkeypatch, tmp_path, volume_data, bootloader)
    sectors = int(volume.size)
    if volume_data['partitions']['type'] == 'gpt':
        # Protective MBR and primary GPT at the start, secondary GPT at the end
        reserved = [(0, 33), (sectors - 33, sectors - 1)]
    else:
        # The MBR
        reserved = [(0, 0)]
    occupied = sorted(ranges + reserved)
    for (_, end), (start, _) in zip(occupied, occupied[1:]):
        assert end < start
    assert occupied[-1][1] < sectors


@pytest.mark.parametrize('volume_data,bootloader', VOLUMES)
def test_parted_uses_computed_boundaries(monkeypatch, tmp_path, volume_data, bootloader):
    volume, ranges = create_partitions(monkeypatch, tmp_path, volume_data, bootloader)
    for partition, (start, end) in zip(volume.partition_map.partitions, ranges):
        assert start == int(partition.get_start() + partition.pad_start)
        # parted includes the end sector, the partition must be exactly as large as computed
        assert end - start + 1 == int(partition.size)


@pytest.mark.parametrize('volume_data,bootloader', VOLUMES)
def test_volume_size_is_sum_of_requested_sizes(volume_data, bootloader):
    requested = sum((Bytes(partition['size']) for name, partition in volume_data['partitions'].items()
                     if name != 'type'), Bytes(0))
    assert load_volume(volume_data, bootloader).size.bytes == requested
