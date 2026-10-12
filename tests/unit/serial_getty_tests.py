import os.path
from types import SimpleNamespace

import pytest

from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common.tasks import grub, initd
from bootstrapvz.common.tools import load_data
from bootstrapvz.providers.azure.tasks import boot as azure_boot
from bootstrapvz.providers.ec2.tasks import boot as ec2_boot
from bootstrapvz.providers.gce.tasks import boot as gce_boot


manifests = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests')


@pytest.mark.parametrize('example', [
    'examples/kvm/buster-cloudimg.yml',
    'examples/ec2/ebs-testing-amd64-pvm.yml',
    'official/gce/jessie.yml',
    'examples/azure/jessie.yml',
    'examples/virtualbox/jessie-vagrant.yml',
    'official/oracle/jessie.yml',
])
@pytest.mark.parametrize('release, enabled', [('wheezy', False), ('jessie', True), ('trixie', True)])
def test_vm_serial_getty(example, release, enabled):
    path = os.path.join(manifests, example)
    data = load_data(path)
    data['system']['release'] = release
    manifest = Manifest(path=path, data=data)

    tasks = load_tasks('resolve_tasks', manifest)

    assert (initd.EnableSerialGetty in tasks) == enabled


@pytest.mark.parametrize('console', ['serial', 'virtual', None])
def test_kvm_console_serial_getty(console):
    path = os.path.join(manifests, 'examples/kvm/buster-cloudimg.yml')
    data = load_data(path)
    if console is None:
        del data['provider']['console']
    else:
        data['provider']['console'] = console
    manifest = Manifest(path=path, data=data)

    assert initd.EnableSerialGetty in load_tasks('resolve_tasks', manifest)


def test_docker_has_no_serial_getty():
    path = os.path.join(manifests, 'examples/docker/stretch.yml')
    manifest = Manifest(path=path)

    assert initd.EnableSerialGetty not in load_tasks('resolve_tasks', manifest)


def test_enable_getty_in_image(monkeypatch):
    calls = []
    monkeypatch.setattr(initd, 'log_check_call', calls.append)
    info = SimpleNamespace(root='/target/image')

    initd.EnableSerialGetty.run(info)

    assert calls == [['chroot', '/target/image', 'systemctl', 'enable', 'serial-getty@ttyS0.service']]


@pytest.mark.parametrize('task', [
    azure_boot.ConfigureGrub,
    ec2_boot.ConfigurePVGrub,
    gce_boot.ConfigureGrub,
])
def test_no_serial_kernel_console(task):
    from bootstrapvz.common.releases import trixie

    info = SimpleNamespace(manifest=SimpleNamespace(release=trixie))
    grub.InitGrubConfig.run(info)

    task.run(info)

    commandline = info.grub_config['GRUB_CMDLINE_LINUX']
    assert not any(option.startswith(('console=ttyS0', 'earlyprintk=ttyS0')) for option in commandline)
