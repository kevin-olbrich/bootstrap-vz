import os
import subprocess

from fysom import FysomError
import pytest

from bootstrapvz.base.fs.exceptions import VolumeError
from bootstrapvz.common.fs import qemuvolume
from bootstrapvz.common.tasks import loopback, volume, workspace
from .boot_volume_tasks_tests import LAYOUTS, NBD_LOADED, build, nbd_host
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
