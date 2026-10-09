import io
import os
import os.path
import stat
import subprocess
import uuid

import pytest

from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.base.fs import load_volume
from bootstrapvz.base.fs.exceptions import PartitionError, VolumeError
from bootstrapvz.base.fs.partitions.base import BasePartition
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common import releases, tools
from bootstrapvz.common.fs import qemuvolume
from bootstrapvz.common.tasks import boot, extlinux, filesystem, grub, kernel, loopback, partitioning
from bootstrapvz.common.tasks import volume, workspace
from bootstrapvz.common.tools import load_data

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/wheezy.yml')

LOOP_DEVICE = '/dev/loop3'

# The volume layouts of the tests, as in the volume.partitions section of a manifest
LAYOUTS = {
    # A separate /boot
    'msdos': {'type': 'msdos',
              'boot': {'filesystem': 'ext2', 'size': '64MiB'},
              'swap': {'size': '128MiB'},
              'root': {'filesystem': 'ext4', 'size': '1GiB', 'mountopts': ['defaults', 'noatime']}},
    # Additional partitions, one of them listed before the partition it is mounted in,
    # /boot is on the root partition
    'gpt': {'type': 'gpt',
            'swap': {'size': '128MiB'},
            'root': {'filesystem': 'ext4', 'size': '1GiB'},
            'tmp': {'filesystem': 'ext4', 'size': '256MiB', 'mountopts': ['nodev', 'nosuid'], 'mode': '1777'},
            'var/tmp': {'filesystem': 'ext4', 'size': '256MiB'},
            'var': {'filesystem': 'xfs', 'size': '512MiB'}},
    'none': {'type': 'none',
             'root': {'filesystem': 'ext4', 'size': '1GiB'}},
}

# The modules that run the commands of the code under test, each one under its own name
COMMAND_RUNNERS = ['bootstrapvz.common.tools',
                   'bootstrapvz.common.tasks.extlinux',
                   'bootstrapvz.common.tasks.filesystem',
                   'bootstrapvz.common.tasks.grub',
                   'bootstrapvz.base.fs.volume',
                   'bootstrapvz.base.fs.partitionmaps.abstract',
                   'bootstrapvz.base.fs.partitionmaps.gpt',
                   'bootstrapvz.base.fs.partitionmaps.msdos',
                   'bootstrapvz.base.fs.partitions.abstract',
                   'bootstrapvz.base.fs.partitions.gpt',
                   'bootstrapvz.base.fs.partitions.gpt_swap',
                   'bootstrapvz.base.fs.partitions.msdos_swap',
                   'bootstrapvz.base.fs.partitions.mount',
                   'bootstrapvz.common.fs.loopbackvolume',
                   'bootstrapvz.common.fs.qemuvolume',
                   ]


def uuid_of(device_path):
    """The filesystem UUID that blkid reports for a device on the fake host"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, device_path))


def mapper(index, volume_name='loop3p'):
    """The device that kpartx maps a partition of the volume to"""
    return '/dev/mapper/{name}{index}'.format(name=volume_name, index=index)


class FakeHost:
    """Records the commands instead of running them and prints the output that the code parses"""

    def __init__(self):
        self.commands = []
        # The commands that got input on stdin, with that input
        self.stdin = []
        # The number of partitions that kpartx finds on a volume
        self.partitions = 0

    def __call__(self, command, stdin=None):
        self.commands.append(command)
        if stdin is not None:
            self.stdin.append((command, stdin))
        if command[:3] == ['losetup', '--show', '--find']:
            return [LOOP_DEVICE]
        if command[:2] == ['kpartx', '-l']:
            name = os.path.basename(command[2])
            # kpartx puts a p between a device name that ends in a digit and the partition number
            if name[-1].isdigit():
                name += 'p'
            return ['{name}{idx} : 0 2048 {device} {offset}'.format(name=name, idx=idx, device=command[2],
                                                                    offset=2048 * idx)
                    for idx in range(1, self.partitions + 1)]
        if command[0] == 'blkid':
            return [uuid_of(command[-1])]
        if command[:2] == ['readlink', '-f']:
            return ['/dev/dm-0' if command[2].startswith('/dev/mapper/') else command[2]]
        return []


@pytest.fixture(autouse=True)
def no_host_commands(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command on the build host: {args}'.format(args=args))
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)


@pytest.fixture(name='host')
def fixture_host(monkeypatch):
    host = FakeHost()
    for module in COMMAND_RUNNERS:
        monkeypatch.setattr(module + '.log_check_call', host)
    # The partitions link their UUIDs in /dev/disk/by-uuid of the build host, leave it alone
    monkeypatch.setattr(BasePartition, 'link_uuid', lambda partition: None)
    monkeypatch.setattr(BasePartition, 'unlink_uuid', lambda partition: None)
    return host


def build(tmp_path, host, partitions, backing='raw'):
    """Returns the bootstrap information of a build before any of its volume tasks ran"""
    # grub cannot boot from unpartitioned volumes
    bootloader = 'extlinux' if partitions['type'] == 'none' else 'grub'
    data = {'backing': backing, 'partitions': partitions}
    info = DictClass(workspace=str(tmp_path / 'workspace'), manifest=DictClass(volume=data),
                     volume=load_volume(data, bootloader), host_dependencies={})
    host.partitions = len(info.volume.partition_map.partitions)
    return info


def prepare_volume(info):
    """Runs the volume tasks of a build up to formatting"""
    workspace.CreateWorkspace.run(info)
    loopback.Create.run(info)
    volume.Attach.run(info)
    if info.manifest.volume['partitions']['type'] != 'none':
        partitioning.PartitionVolume.run(info)
        partitioning.MapPartitions.run(info)
    filesystem.Format.run(info)


def mount_volume(info):
    """Runs the mounting tasks of a build"""
    filesystem.CreateMountDir.run(info)
    filesystem.MountRoot.run(info)
    if 'boot' in info.manifest.volume['partitions']:
        filesystem.CreateBootMountDir.run(info)
        filesystem.MountBoot.run(info)
    filesystem.MountAdditional.run(info)
    filesystem.ChmodMountDirs.run(info)
    filesystem.MountSpecials.run(info)


def resolve_tasks(partitions, bootloader, release):
    data = load_data(example)
    data['system']['release'] = release
    data['system']['bootloader'] = bootloader
    data['volume']['partitions'] = partitions
    return load_tasks('resolve_tasks', Manifest(path=example, data=data))


def test_workspace_is_created_and_deleted(tmp_path):
    info = DictClass(workspace=str(tmp_path / 'target/0123abcd'))
    workspace.CreateWorkspace.run(info)
    assert os.path.isdir(info.workspace)
    workspace.DeleteWorkspace.run(info)
    assert not os.path.exists(info.workspace)
    assert os.path.isdir(tmp_path / 'target')


def test_workspace_with_files_is_kept(tmp_path):
    info = DictClass(workspace=str(tmp_path / 'workspace'))
    workspace.CreateWorkspace.run(info)
    image = tmp_path / 'workspace/volume.raw'
    image.write_bytes(b'image')
    # Deleting the workspace when rolling back must not take a leftover image with it
    with pytest.raises(OSError):
        workspace.DeleteWorkspace.run(info)
    assert image.read_bytes() == b'image'


@pytest.mark.parametrize('layout,backing,dependencies', [
    ('msdos', 'raw', {'losetup': 'mount', 'truncate': 'coreutils', 'parted': 'parted', 'kpartx': 'kpartx'}),
    ('gpt', 'qcow2', {'losetup': 'mount', 'truncate': 'coreutils', 'qemu-img': 'qemu-utils',
                      'parted': 'parted', 'kpartx': 'kpartx', 'mkfs.xfs': 'xfsprogs'}),
    ('none', 'vmdk', {'losetup': 'mount', 'truncate': 'coreutils', 'qemu-img': 'qemu-utils'}),
])
def test_host_dependencies(tmp_path, host, layout, backing, dependencies):
    info = build(tmp_path, host, LAYOUTS[layout], backing)
    loopback.AddRequiredCommands.run(info)
    partitioning.AddRequiredCommands.run(info)
    filesystem.AddRequiredCommands.run(info)
    assert info.host_dependencies == dependencies


@pytest.mark.parametrize('backing,command', [
    ('raw', ['truncate', '--size=1216M', '{workspace}/volume.raw']),
    ('qcow2', ['qemu-img', 'create', '-f', 'qcow2', '{workspace}/volume.qcow2', '1216M']),
    ('vdi', ['qemu-img', 'create', '-f', 'vdi', '{workspace}/volume.vdi', '1216M']),
    ('vmdk', ['qemu-img', 'create', '-f', 'vmdk', '{workspace}/volume.vmdk', '1216M']),
])
def test_image_is_created_in_the_workspace(tmp_path, host, backing, command):
    info = build(tmp_path, host, LAYOUTS['msdos'], backing)
    loopback.Create.run(info)
    assert host.commands == [[arg.format(workspace=info.workspace) for arg in command]]


def test_raw_image_is_attached_detached_and_deleted(tmp_path, host):
    info = build(tmp_path, host, LAYOUTS['msdos'])
    workspace.CreateWorkspace.run(info)
    loopback.Create.run(info)
    image = os.path.join(info.workspace, 'volume.raw')
    # truncate does not run, so the image is not there yet
    with open(image, 'wb'):
        pass
    host.commands.clear()
    volume.Attach.run(info)
    assert host.commands == [['losetup', '--show', '--find', '--partscan', image]]
    assert info.volume.device_path == LOOP_DEVICE
    host.commands.clear()
    volume.Detach.run(info)
    assert host.commands == [['losetup', '--detach', LOOP_DEVICE]]
    assert info.volume.device_path is None
    volume.Delete.run(info)
    assert not os.path.exists(image)
    workspace.DeleteWorkspace.run(info)


def test_unpartitioned_root_is_the_volume_device(tmp_path, host):
    info = build(tmp_path, host, LAYOUTS['none'])
    loopback.Create.run(info)
    volume.Attach.run(info)
    root = info.volume.partition_map.root
    assert root.device_path == LOOP_DEVICE
    volume.Detach.run(info)
    assert root.device_path is None


def nbd_host(monkeypatch, modules, max_part):
    """Lets QEMU volumes see an nbd module with the given max_part and nbd0 in use"""
    files = {'/proc/modules': modules,
             '/sys/module/nbd/parameters/max_part': max_part + '\n',
             '/sys/block/nbd1/size': '2490368\n'}

    def host_open(path, *args, **kwargs):
        if path not in files:
            raise FileNotFoundError(path)
        return io.StringIO(files[path])
    monkeypatch.setattr(qemuvolume, 'open', host_open, raising=False)
    monkeypatch.setattr(qemuvolume, 'get_partitions', lambda: {'nbd0': {'major': '43', 'minor': '0'}})


NBD_LOADED = 'nbd 57344 0 - Live 0x0000000000000000\nloop 32768 0 - Live 0x0000000000000000\n'


def test_qemu_image_is_attached_to_a_free_nbd_device(tmp_path, monkeypatch, host):
    info = build(tmp_path, host, LAYOUTS['msdos'], 'qcow2')
    nbd_host(monkeypatch, NBD_LOADED, '16')
    loopback.Create.run(info)
    host.commands.clear()
    volume.Attach.run(info)
    [command] = host.commands
    assert command[0] == 'qemu-nbd'
    assert command[-3:] == ['--connect', '/dev/nbd1', os.path.join(info.workspace, 'volume.qcow2')]
    assert info.volume.device_path == '/dev/nbd1'
    host.commands.clear()
    volume.Detach.run(info)
    assert host.commands == [['qemu-nbd', '--disconnect', '/dev/nbd1']]


@pytest.mark.parametrize('modules,max_part,message', [
    ('loop 32768 0 - Live 0x0000000000000000\n', '16', 'must be loaded .*modprobe nbd max_part=3'),
    (NBD_LOADED, '2', 'set to 2,.*modprobe nbd max_part=3'),
], ids=['nbd-not-loaded', 'max-part-too-low'])
def test_qemu_image_needs_nbd_with_enough_partitions(tmp_path, monkeypatch, host, modules, max_part, message):
    info = build(tmp_path, host, LAYOUTS['msdos'], 'qcow2')
    nbd_host(monkeypatch, modules, max_part)
    loopback.Create.run(info)
    host.commands.clear()
    with pytest.raises(VolumeError, match=message):
        volume.Attach.run(info)
    assert host.commands == []


@pytest.mark.parametrize('partitions,bootloader,commands', [
    (LAYOUTS['msdos'], 'grub',
     ['mklabel msdos', 'mkpart primary ext2', 'set 1 boot on',
      'mkpart primary linux-swap', 'mkpart primary ext2']),
    (dict(LAYOUTS['none'], type='msdos'), 'extlinux',
     ['mklabel msdos', 'mkpart primary ext2', 'set 1 boot on']),
    (LAYOUTS['gpt'], 'grub',
     ['mklabel gpt', 'mkpart primary ext2', 'set 1 bios_grub on',
      'mkpart primary linux-swap', 'name 2 swap', 'mkpart primary ext2', 'name 3 root',
      'mkpart primary ext2', 'name 4 tmp', 'mkpart primary ext2', 'name 5 var/tmp',
      'mkpart primary ext2', 'name 6 var']),
    (dict(LAYOUTS['msdos'], type='gpt'), 'extlinux',
     ['mklabel gpt', 'mkpart primary ext2', 'set 1 legacy_boot on', 'name 1 boot',
      'mkpart primary linux-swap', 'name 2 swap', 'mkpart primary ext2', 'name 3 root']),
    (LAYOUTS['gpt'], 'extlinux',
     ['mklabel gpt', 'mkpart primary linux-swap', 'name 1 swap',
      'mkpart primary ext2', 'set 2 legacy_boot on', 'name 2 root',
      'mkpart primary ext2', 'name 3 tmp', 'mkpart primary ext2', 'name 4 var/tmp',
      'mkpart primary ext2', 'name 5 var']),
], ids=['msdos-boot-grub', 'msdos-extlinux', 'gpt-grub', 'gpt-boot-extlinux', 'gpt-extlinux'])
def test_partition_table(host, partitions, bootloader, commands):
    data = {'backing': 'raw', 'partitions': partitions}
    info = DictClass(volume=load_volume(data, bootloader))
    info.volume.device_path = LOOP_DEVICE
    partitioning.PartitionVolume.run(info)
    for command in host.commands:
        assert command[:2] == ['parted', '--script']
        assert command[command.index('--') - 1] == LOOP_DEVICE
    # The partition boundaries are tested in partition_maps_tests
    issued = [' '.join(command[command.index('--') + 1:]) for command in host.commands]
    assert [command.rsplit(' ', 2)[0] if command.startswith('mkpart ') else command
            for command in issued] == commands


@pytest.mark.parametrize('layout', ['msdos', 'gpt'])
def test_partitions_are_mapped(tmp_path, host, layout):
    info = build(tmp_path, host, LAYOUTS[layout])
    info.volume.device_path = LOOP_DEVICE
    partitioning.PartitionVolume.run(info)
    host.commands.clear()
    partitioning.MapPartitions.run(info)
    assert host.commands == [['kpartx', '-l', LOOP_DEVICE], ['kpartx', '-as', LOOP_DEVICE]]
    partitions = info.volume.partition_map.partitions
    assert [partition.device_path for partition in partitions] \
        == [mapper(index) for index in range(1, len(partitions) + 1)]


def test_partial_mapping_is_reverted(tmp_path, host):
    info = build(tmp_path, host, LAYOUTS['msdos'])
    info.volume.device_path = LOOP_DEVICE
    partitioning.PartitionVolume.run(info)
    host.partitions = 2
    host.commands.clear()
    with pytest.raises(PartitionError, match='kpartx did not map partition #3'):
        partitioning.MapPartitions.run(info)
    assert host.commands[-1] == ['kpartx', '-ds', LOOP_DEVICE]
    assert [partition.device_path for partition in info.volume.partition_map.partitions] == [None] * 3


def test_mapped_partitions_keep_the_volume_attached(tmp_path, host):
    info = build(tmp_path, host, LAYOUTS['msdos'])
    prepare_volume(info)
    with pytest.raises(VolumeError):
        volume.Detach.run(info)
    host.commands.clear()
    partitioning.UnmapPartitions.run(info)
    volume.Detach.run(info)
    assert host.commands == [['kpartx', '-ds', LOOP_DEVICE], ['losetup', '--detach', LOOP_DEVICE]]


@pytest.mark.parametrize('layout,commands', [
    ('msdos', [['mkfs.ext2', mapper(1)], ['mkswap', mapper(2)], ['mkfs.ext4', mapper(3)]]),
    # The bios_grub partition stays unformatted
    ('gpt', [['mkswap', mapper(2)], ['mkfs.ext4', mapper(3)], ['mkfs.ext4', mapper(4)],
             ['mkfs.ext4', mapper(5)], ['mkfs.xfs', mapper(6)]]),
    ('none', [['mkfs.ext4', LOOP_DEVICE]]),
])
def test_partitions_are_formatted(tmp_path, host, layout, commands):
    info = build(tmp_path, host, LAYOUTS[layout])
    prepare_volume(info)
    assert host.commands[-len(commands):] == commands
    assert not any(command[0].startswith('mkfs') or command[0] == 'mkswap'
                   for command in host.commands[:-len(commands)])


def test_format_command_gets_the_partition_variables(tmp_path, host):
    root = dict(LAYOUTS['none']['root'],
                format_command=['mkfs.{fs}', '-L', 'root', '{device_path}', '{size}'])
    info = build(tmp_path, host, {'type': 'none', 'root': root})
    prepare_volume(info)
    assert host.commands[-1] == ['mkfs.ext4', '-L', 'root', LOOP_DEVICE, '2097152s']


@pytest.mark.parametrize('layout,devices', [
    ('msdos', [mapper(1), mapper(3)]),
    # Neither swap, xfs, nor the bios_grub partition
    ('gpt', [mapper(3), mapper(4), mapper(5)]),
    ('none', [LOOP_DEVICE]),
])
def test_time_based_fsck_is_disabled_on_ext_filesystems(tmp_path, host, layout, devices):
    info = build(tmp_path, host, LAYOUTS[layout])
    prepare_volume(info)
    host.commands.clear()
    filesystem.TuneVolumeFS.run(info)
    assert host.commands == [['tune2fs', '-i', '0', device] for device in devices]


@pytest.mark.parametrize('layout,mounts', [
    ('msdos', [['--options', 'defaults,noatime', '--types', 'ext4', mapper(3), ''],
               ['--types', 'ext2', mapper(1), 'boot']]),
    # Additional partitions are mounted after the partitions they are mounted in
    ('gpt', [['--types', 'ext4', mapper(3), ''],
             ['--options', 'nodev,nosuid', '--types', 'ext4', mapper(4), 'tmp'],
             ['--types', 'xfs', mapper(6), 'var'],
             ['--types', 'ext4', mapper(5), 'var/tmp']]),
    ('none', [['--types', 'ext4', LOOP_DEVICE, '']]),
])
def test_volume_is_mounted(tmp_path, host, layout, mounts):
    info = build(tmp_path, host, LAYOUTS[layout])
    prepare_volume(info)
    host.commands.clear()
    mount_volume(info)
    root = os.path.join(info.workspace, 'root')
    assert info.root == root
    specials = [['--bind', '/dev', 'dev'],
                ['--types', 'proc', 'none', 'proc'],
                ['--types', 'sysfs', 'none', 'sys'],
                ['--types', 'devpts', 'none', 'dev/pts']]
    assert host.commands == [['mount'] + mount[:-1] + [os.path.normpath(os.path.join(root, mount[-1]))]
                             for mount in mounts + specials]
    for mount in mounts:
        assert os.path.isdir(os.path.join(root, mount[-1]))


def test_mount_dir_mode(tmp_path, host):
    info = build(tmp_path, host, LAYOUTS['gpt'])
    prepare_volume(info)
    mount_volume(info)
    assert stat.S_IMODE(os.stat(os.path.join(info.root, 'tmp')).st_mode) == 0o1777


@pytest.mark.parametrize('layout', ['msdos', 'gpt', 'none'])
def test_volume_is_unmounted(tmp_path, host, layout):
    info = build(tmp_path, host, LAYOUTS[layout])
    prepare_volume(info)
    mount_volume(info)
    mounted = [command[-1] for command in host.commands if command[0] == 'mount']
    host.commands.clear()
    filesystem.UnmountRoot.run(info)
    assert all(command[0] == 'umount' for command in host.commands)
    unmounted = [command[1] for command in host.commands]
    assert sorted(unmounted) == sorted(mounted)
    # Everything that is mounted inside a mount is unmounted before it, the root goes last
    for index, path in enumerate(unmounted):
        assert not any(nested.startswith(path + '/') for nested in unmounted[index + 1:])
    assert all(partition.fsm.current != 'mounted' for partition in info.volume.partition_map.partitions)


def test_mount_dir_is_deleted(tmp_path, host):
    info = build(tmp_path, host, LAYOUTS['msdos'])
    workspace.CreateWorkspace.run(info)
    filesystem.CreateMountDir.run(info)
    root = info.root
    filesystem.DeleteMountDir.run(info)
    assert not os.path.exists(root)
    assert 'root' not in info


@pytest.mark.parametrize('layout,lines', [
    ('msdos', ['UUID={} / ext4 defaults,noatime 1 1'.format(uuid_of(mapper(3))),
               'UUID={} /boot ext2 defaults 1 2'.format(uuid_of(mapper(1))),
               'UUID={} none swap defaults 1 0'.format(uuid_of(mapper(2)))]),
    ('gpt', ['UUID={} / ext4 defaults 1 1'.format(uuid_of(mapper(3))),
             'UUID={} none swap defaults 1 0'.format(uuid_of(mapper(2))),
             'UUID={} /tmp ext4 nodev,nosuid 1 2'.format(uuid_of(mapper(4))),
             'UUID={} /var xfs defaults 1 2'.format(uuid_of(mapper(6))),
             'UUID={} /var/tmp ext4 defaults 1 2'.format(uuid_of(mapper(5)))]),
])
def test_fstab(tmp_path, host, layout, lines):
    info = build(tmp_path, host, LAYOUTS[layout])
    prepare_volume(info)
    mount_volume(info)
    os.mkdir(os.path.join(info.root, 'etc'))
    filesystem.FStab.run(info)
    with open(os.path.join(info.root, 'etc/fstab'), encoding='utf-8') as fstab:
        assert fstab.read() == '\n'.join(lines) + '\n'


@pytest.mark.parametrize('layout', ['msdos', 'gpt', 'none'])
def test_volume_tasks_are_resolved_for_the_layout(layout):
    tasks = resolve_tasks(LAYOUTS[layout], 'extlinux', 'wheezy')
    partitioning_tasks = {partitioning.AddRequiredCommands, partitioning.PartitionVolume,
                          partitioning.MapPartitions, partitioning.UnmapPartitions}
    boot_tasks = {filesystem.CreateBootMountDir, filesystem.MountBoot}
    assert tasks & partitioning_tasks == (set() if layout == 'none' else partitioning_tasks)
    assert tasks & boot_tasks == (boot_tasks if 'boot' in LAYOUTS[layout] else set())


@pytest.mark.parametrize('bootloader,release,expected', [
    ('grub', 'wheezy', {grub.InstallGrub_1_99}),
    ('grub', 'jessie', {grub.InstallGrub_2}),
    ('grub', 'trixie', {grub.InstallGrub_2}),
    ('extlinux', 'wheezy', {extlinux.ConfigureExtlinux, extlinux.InstallExtlinux}),
    ('extlinux', 'jessie', {extlinux.ConfigureExtlinuxJessie, extlinux.InstallExtlinuxJessie}),
    ('extlinux', 'trixie', {extlinux.ConfigureExtlinuxJessie, extlinux.InstallExtlinuxJessie}),
])
def test_bootloader_installation_is_resolved_for_the_release(bootloader, release, expected):
    installation = {grub.InstallGrub_1_99, grub.InstallGrub_2,
                    extlinux.ConfigureExtlinux, extlinux.InstallExtlinux,
                    extlinux.ConfigureExtlinuxJessie, extlinux.InstallExtlinuxJessie}
    assert resolve_tasks(LAYOUTS['msdos'], bootloader, release) & installation == expected


@pytest.mark.parametrize('task,partition_type,architecture,packages', [
    (grub.AddGrubPackage, 'msdos', 'amd64', {'grub-pc'}),
    (extlinux.AddExtlinuxPackage, 'msdos', 'amd64', {'extlinux'}),
    (extlinux.AddExtlinuxPackage, 'gpt', 'amd64', {'extlinux', 'syslinux-common'}),
    (filesystem.AddXFSProgs, 'msdos', 'amd64', {'xfsprogs'}),
    (kernel.AddDKMSPackages, 'msdos', 'amd64', {'dkms', 'linux-headers-amd64'}),
    (kernel.AddDKMSPackages, 'msdos', 'i386', {'dkms', 'linux-headers-686-pae'}),
])
def test_packages(task, partition_type, architecture, packages):
    data = {'backing': 'raw', 'partitions': dict(LAYOUTS['msdos'], type=partition_type)}
    info = DictClass(packages=set(), volume=load_volume(data, 'extlinux'),
                     manifest=DictClass(system={'architecture': architecture}))
    task.run(info)
    assert info.packages == packages


@pytest.mark.parametrize('layout', ['msdos', 'gpt'])
def test_grub_2_is_installed_on_the_volume(tmp_path, host, layout):
    info = build(tmp_path, host, LAYOUTS[layout])
    info.root = str(tmp_path / 'root')
    info.volume.device_path = LOOP_DEVICE
    grub.InstallGrub_2.run(info)
    assert host.commands == [['chroot', info.root, 'grub-install', LOOP_DEVICE],
                             ['chroot', info.root, 'update-grub']]


# The size of the volumes in sectors
@pytest.mark.parametrize('layout,prefix,sectors', [('msdos', 'msdos', 1216 * 2048),
                                                   ('gpt', 'gpt', 2176 * 2048)])
def test_grub_1_99_is_installed_through_a_device_mapper_node(tmp_path, monkeypatch, host,
                                                             layout, prefix, sectors):
    info = build(tmp_path, host, LAYOUTS[layout])
    prepare_volume(info)
    mount_volume(info)
    mounts = [command for command in host.commands if command[0] == 'mount']
    os.makedirs(os.path.join(info.root, 'boot/grub'), exist_ok=True)
    # The host lists the loop device in /proc/partitions and has no device mapper nodes yet
    monkeypatch.setattr('bootstrapvz.common.fs.get_partitions',
                        lambda: {'loop3': {'major': '7', 'minor': '3'}})
    exists = os.path.exists
    monkeypatch.setattr(os.path, 'exists',
                        lambda path: not str(path).startswith('/dev/mapper/') and exists(path))
    host.commands.clear()
    grub.InstallGrub_1_99.run(info)

    partitions = info.volume.partition_map.partitions
    with open(os.path.join(info.root, 'boot/grub/device.map'), encoding='utf-8') as device_map:
        assert device_map.read().splitlines() == ['(hd0) /dev/dm-0'] + [
            '(hd0,{prefix}{idx}) {device}'.format(prefix=prefix, idx=idx, device=mapper(idx, 'vda'))
            for idx in range(1, len(partitions) + 1)]
    vda = '/dev/mapper/vda'
    assert [command for command in host.commands if command[0] in ['kpartx', 'dmsetup', 'chroot']] == [
        ['kpartx', '-ds', LOOP_DEVICE],
        ['dmsetup', 'create', 'vda'],
        ['kpartx', '-l', vda],
        ['kpartx', '-as', vda],
        ['chroot', info.root, 'grub-install', '/dev/dm-0'],
        ['chroot', info.root, 'update-grub'],
        ['kpartx', '-ds', vda],
        ['dmsetup', 'remove', 'vda'],
        ['kpartx', '-l', LOOP_DEVICE],
        ['kpartx', '-as', LOOP_DEVICE]]
    assert host.stdin == [(['dmsetup', 'create', 'vda'], '0 {sectors} linear 7:3 0'.format(sectors=sectors))]
    # The volume is back on the loop device and mounted as before
    assert info.volume.device_path == LOOP_DEVICE
    assert [partition.device_path for partition in partitions] \
        == [mapper(idx) for idx in range(1, len(partitions) + 1)]
    remounts = host.commands[host.commands.index(['kpartx', '-as', LOOP_DEVICE]) + 1:]
    assert sorted(remounts) == sorted(mounts)


def test_extlinux_gets_a_serial_console_on_wheezy(tmp_path):
    (tmp_path / 'etc/default').mkdir(parents=True)
    defaults = tmp_path / 'etc/default/extlinux'
    defaults.write_text('EXTLINUX_UPDATE="true"\nEXTLINUX_PARAMETERS="ro quiet"\nEXTLINUX_TIMEOUT="50"\n',
                        encoding='utf-8')
    extlinux.ConfigureExtlinux.run(DictClass(root=str(tmp_path)))
    assert defaults.read_text(encoding='utf-8') \
        == 'EXTLINUX_UPDATE="true"\nEXTLINUX_PARAMETERS="ro quiet console=ttyS0"\nEXTLINUX_TIMEOUT="50"\n'


@pytest.mark.parametrize('task,partition_type,mbr,update', [
    (extlinux.InstallExtlinux, 'msdos', '/usr/lib/extlinux/mbr.bin', True),
    (extlinux.InstallExtlinux, 'gpt', '/usr/lib/syslinux/gptmbr.bin', True),
    (extlinux.InstallExtlinuxJessie, 'msdos', '/usr/lib/EXTLINUX/mbr.bin', False),
    (extlinux.InstallExtlinuxJessie, 'gpt', '/usr/lib/EXTLINUX/gptmbr.bin', False),
])
def test_extlinux_is_installed_on_the_volume(tmp_path, host, task, partition_type, mbr, update):
    data = {'backing': 'raw', 'partitions': dict(LAYOUTS['msdos'], type=partition_type)}
    info = DictClass(root=str(tmp_path), volume=load_volume(data, 'extlinux'))
    info.volume.device_path = LOOP_DEVICE
    task.run(info)
    commands = [['chroot', info.root, 'dd', 'bs=440', 'count=1', 'if=' + mbr, 'of=' + LOOP_DEVICE],
                ['chroot', info.root, 'extlinux', '--install', '/boot/extlinux']]
    if update:
        commands.append(['chroot', info.root, 'extlinux-update'])
    assert host.commands == commands


@pytest.mark.parametrize('task,command', [
    (kernel.UpdateInitramfs, ['update-initramfs', '-u', '-k', 'all']),
    (boot.UpdateInitramfs, ['update-initramfs', '-u']),
])
def test_initramfs_is_rebuilt(tmp_path, host, task, command):
    task.run(DictClass(root=str(tmp_path)))
    assert host.commands == [['chroot', str(tmp_path)] + command]


def test_kernel_version_is_the_newest_vmlinuz(tmp_path):
    boot_dir = tmp_path / 'boot'
    boot_dir.mkdir()
    for name in ['vmlinuz-5.10.0-28-amd64', 'vmlinuz-6.1.0-18-amd64', 'initrd.img-6.1.0-18-amd64',
                 'config-6.1.0-18-amd64', 'System.map-6.1.0-18-amd64']:
        (boot_dir / name).write_bytes(b'')
    # The default kernel symlinks are no kernel versions
    (boot_dir / 'vmlinuz').symlink_to('vmlinuz-6.1.0-18-amd64')
    (boot_dir / 'vmlinuz.old').symlink_to('vmlinuz-5.10.0-28-amd64')
    info = DictClass(root=str(tmp_path))
    kernel.DetermineKernelVersion.run(info)
    assert info.kernel_version == '6.1.0-18-amd64'


def test_pc_speaker_and_floppy_modules_are_blacklisted(tmp_path):
    (tmp_path / 'etc/modprobe.d').mkdir(parents=True)
    blacklist = tmp_path / 'etc/modprobe.d/blacklist.conf'
    blacklist.write_text('blacklist evbug\n', encoding='utf-8')
    boot.BlackListModules.run(DictClass(root=str(tmp_path)))
    assert blacklist.read_text(encoding='utf-8').splitlines() \
        == ['blacklist evbug', '# disable pc speaker and floppy', 'blacklist pcspkr', 'blacklist floppy']


def test_gettys_are_disabled_in_inittab_on_wheezy(tmp_path):
    (tmp_path / 'etc').mkdir()
    inittab = tmp_path / 'etc/inittab'
    gettys = ['1:2345:respawn:/sbin/getty 38400 tty1'] + \
        ['{tty}:23:respawn:/sbin/getty 38400 tty{tty}'.format(tty=tty) for tty in range(2, 7)]
    serial = 'T0:23:respawn:/sbin/getty -L ttyS0 9600 vt100'
    inittab.write_text('\n'.join(gettys + [serial]) + '\n', encoding='utf-8')
    boot.DisableGetTTYs.run(DictClass(root=str(tmp_path),
                                      manifest=DictClass(release=releases.get_release('wheezy'))))
    assert inittab.read_text(encoding='utf-8').splitlines() == ['#' + getty for getty in gettys] + [serial]


def test_gettys_are_disabled_in_logind_from_jessie(tmp_path):
    (tmp_path / 'etc/systemd').mkdir(parents=True)
    boot.DisableGetTTYs.run(DictClass(root=str(tmp_path),
                                      manifest=DictClass(release=releases.get_release('jessie'))))
    logind = (tmp_path / 'etc/systemd/logind.conf').read_text(encoding='utf-8').splitlines()
    assert logind[logind.index('[Login]') + 1:] == ['# Disable all TTY getters', 'NAutoVTs=0', 'ReserveVT=0']
