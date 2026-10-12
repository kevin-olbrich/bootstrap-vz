import os
import subprocess

from fysom import FysomError
import pytest

from bootstrapvz.base.fs.exceptions import VolumeError
from bootstrapvz.common.fs import qemuvolume, unmounted
from bootstrapvz.common.tasks import filesystem, loopback, partitioning, volume, workspace
from .boot_volume_tasks_tests import (LAYOUTS, LOOP_DEVICE, NBD_LOADED, build, mount_volume,
                                     nbd_host, prepare_volume)
from .boot_volume_tasks_tests import fixture_host, no_host_commands

# Re-export the shared fixtures so pytest registers them for this module.
__all__ = ['fixture_host', 'no_host_commands']


@pytest.mark.parametrize('error_type', [VolumeError, KeyboardInterrupt])
def test_qemu_readiness_failure_disconnects_device(tmp_path, monkeypatch, host, error_type):
    info = build(tmp_path, host, LAYOUTS['msdos'], 'qcow2')
    nbd_host(monkeypatch, NBD_LOADED, '16')
    loopback.Create.run(info)
    failure = error_type('NBD device did not become ready')

    def fail_readiness(device_path):
        assert device_path == '/dev/nbd1'
        raise failure
    monkeypatch.setattr(info.volume, '_wait_for_nbd_device', fail_readiness)
    host.commands.clear()
    with pytest.raises(error_type) as raised:
        volume.Attach.run(info)
    assert raised.value is failure
    assert host.commands == [['qemu-nbd', '--connect', '/dev/nbd1', info.volume.image_path],
                             ['qemu-nbd', '--disconnect', '/dev/nbd1']]
    assert info.volume.fsm.current == 'detached'
    assert info.volume.device_path is None
    assert not hasattr(info.volume, 'loop_device_path')


def test_qemu_failed_connect_does_not_disconnect_another_volume(tmp_path, monkeypatch, host):
    info = build(tmp_path, host, LAYOUTS['msdos'], 'qcow2')
    nbd_host(monkeypatch, NBD_LOADED, '16')
    loopback.Create.run(info)
    command = ['qemu-nbd', '--connect', '/dev/nbd1', info.volume.image_path]
    failure = subprocess.CalledProcessError(1, command)

    def fail_connect(args):
        host(args)
        raise failure
    monkeypatch.setattr(qemuvolume, 'log_check_call', fail_connect)
    host.commands.clear()
    with pytest.raises(subprocess.CalledProcessError) as raised:
        volume.Attach.run(info)
    assert raised.value is failure
    assert host.commands == [command]
    assert info.volume.fsm.current == 'detached'
    assert info.volume.device_path is None
    assert not hasattr(info.volume, 'loop_device_path')


@pytest.mark.parametrize('error_type', [OSError, KeyboardInterrupt])
def test_qemu_failed_disconnect_preserves_backing_image(tmp_path, monkeypatch, host, error_type):
    info = build(tmp_path, host, LAYOUTS['msdos'], 'qcow2')
    nbd_host(monkeypatch, NBD_LOADED, '16')
    workspace.CreateWorkspace.run(info)
    loopback.Create.run(info)
    image_path = info.volume.image_path
    with open(image_path, 'wb'):
        pass
    failure = VolumeError('NBD device did not become ready')
    cleanup_failure = error_type('NBD disconnect failed')

    def fail_readiness(device_path):
        raise failure

    def fail_disconnect(command):
        result = host(command)
        if command[:2] == ['qemu-nbd', '--disconnect']:
            raise cleanup_failure
        return result
    monkeypatch.setattr(info.volume, '_wait_for_nbd_device', fail_readiness)
    monkeypatch.setattr(qemuvolume, 'log_check_call', fail_disconnect)
    host.commands.clear()
    with pytest.raises(error_type) as raised:
        volume.Attach.run(info)
    assert raised.value is cleanup_failure
    assert raised.value.__context__ is failure
    assert host.commands == [['qemu-nbd', '--connect', '/dev/nbd1', image_path],
                             ['qemu-nbd', '--disconnect', '/dev/nbd1']]
    assert info.volume.fsm.current == 'attached'
    assert info.volume.device_path == info.volume.loop_device_path == '/dev/nbd1'
    with pytest.raises(FysomError):
        volume.Delete.run(info)
    assert os.path.exists(image_path)
    monkeypatch.setattr(qemuvolume, 'log_check_call', host)
    volume.Detach.run(info)
    assert info.volume.fsm.current == 'detached'
    assert info.volume.device_path is None
    assert not hasattr(info.volume, 'loop_device_path')
    volume.Delete.run(info)
    assert not os.path.exists(image_path)


@pytest.mark.parametrize('layout', ['msdos', 'gpt', 'none'])
@pytest.mark.parametrize('error_type', [OSError, KeyboardInterrupt])
def test_temporary_unmount_restores_mounts_after_body_failure(tmp_path, host, layout, error_type):
    info = build(tmp_path, host, LAYOUTS[layout])
    prepare_volume(info)
    mount_volume(info)
    mounts = [command for command in host.commands if command[0] == 'mount']
    p_map = info.volume.partition_map
    error = error_type('operation failed')
    host.commands.clear()

    with pytest.raises(error_type) as caught:
        with unmounted(info.volume):
            assert all(partition.fsm.current != 'mounted' for partition in p_map.partitions)
            if layout != 'none':
                assert p_map.fsm.current == 'unmapped'
            raise error
    assert caught.value is error
    assert p_map.root.fsm.current == 'mounted'
    assert p_map.root.mount_dir == info.root
    assert sorted(command for command in host.commands if command[0] == 'mount') == sorted(mounts)
    filesystem.UnmountRoot.run(info)
    if layout != 'none':
        assert p_map.fsm.current == 'mapped'
        partitioning.UnmapPartitions.run(info)
    volume.Detach.run(info)
    assert info.volume.fsm.current == 'detached'


def test_temporary_unmount_does_not_mount_after_remapping_fails(tmp_path, host, monkeypatch):
    info = build(tmp_path, host, LAYOUTS['msdos'])
    prepare_volume(info)
    mount_volume(info)
    command = ['kpartx', '-as', LOOP_DEVICE]
    error = subprocess.CalledProcessError(1, command)
    host.commands.clear()

    def remap(mapping_command):
        result = host(mapping_command)
        if mapping_command == command:
            raise error
        return result

    monkeypatch.setattr('bootstrapvz.base.fs.partitionmaps.abstract.log_check_call', remap)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        with unmounted(info.volume):
            pass
    assert caught.value is error
    assert not any(recorded[0] == 'mount' for recorded in host.commands)
    assert info.volume.partition_map.root.fsm.current == 'unmapped_fmt'


def test_temporary_unmount_entry_failure_can_retry_teardown(tmp_path, host, monkeypatch):
    info = build(tmp_path, host, LAYOUTS['msdos'])
    prepare_volume(info)
    mount_volume(info)
    command = ['kpartx', '-ds', LOOP_DEVICE]
    error = subprocess.CalledProcessError(1, command, 'device is busy')
    host.commands.clear()

    def unmap(mapping_command):
        result = host(mapping_command)
        if mapping_command == command and host.commands.count(command) == 1:
            raise error
        return result

    monkeypatch.setattr('bootstrapvz.base.fs.partitionmaps.abstract.log_check_call', unmap)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        with unmounted(info.volume):
            pytest.fail('operation ran after unmap failed')
    assert caught.value is error
    assert not any(recorded[0] == 'mount' for recorded in host.commands)
    assert info.volume.partition_map.root.fsm.current == 'formatted'
    filesystem.UnmountRoot.run(info)
    partitioning.UnmapPartitions.run(info)
    volume.Detach.run(info)
    assert host.commands.count(command) == 2
    assert info.volume.fsm.current == 'detached'


def test_copy_image_failure_restores_mounts(tmp_path, host, monkeypatch):
    from bootstrapvz.plugins.prebootstrapped import tasks

    info = build(tmp_path, host, LAYOUTS['msdos'])
    info.run_id = 'snapshot-test'
    info.manifest.bootstrapper = {'workspace': str(tmp_path)}
    prepare_volume(info)
    mount_volume(info)
    mounts = [command for command in host.commands if command[0] == 'mount']
    error = OSError('no space for image copy')
    host.commands.clear()

    def copyfile(source, destination):
        assert source == info.volume.image_path
        assert destination == str(tmp_path / 'volume-snapshot-test.raw.backup')
        assert info.volume.partition_map.fsm.current == 'unmapped'
        raise error

    monkeypatch.setattr(tasks, 'copyfile', copyfile)
    with pytest.raises(OSError) as caught:
        tasks.CopyImage.run(info)
    assert caught.value is error
    assert info.volume.partition_map.root.fsm.current == 'mounted'
    assert sorted(command for command in host.commands if command[0] == 'mount') == sorted(mounts)
