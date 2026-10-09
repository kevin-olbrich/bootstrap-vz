import os.path
import subprocess

import pytest

from bootstrapvz.base.bootstrapinfo import BootstrapInformation
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import create_list, get_all_tasks, load_tasks
from bootstrapvz.common import tools
from bootstrapvz.common.tasks import grub
from bootstrapvz.common.tools import load_data
from bootstrapvz.providers.kvm.tasks import boot, packages

examples = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests/examples/kvm')


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {args}'.format(args=args))
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)


def bootstrap_info(name, root, **system):
    path = os.path.join(examples, name)
    data = load_data(path)
    data['system'].update(system)
    info = BootstrapInformation(Manifest(path=path, data=data))
    info.root = str(root)
    return info


def grub_defaults(info):
    """Runs the tasks that fill the grub configuration in tasklist order and returns /etc/default/grub"""
    manifest = info.manifest
    all_tasks = set(get_all_tasks([manifest.modules['provider']] + manifest.modules['plugins']))
    os.makedirs(os.path.join(info.root, 'etc/default'))
    for task in create_list(load_tasks('resolve_tasks', manifest), all_tasks):
        if task in (grub.InitGrubConfig, grub.WriteGrubConfig) or grub.WriteGrubConfig in task.successors:
            task.run(info)
    settings = {}
    with open(os.path.join(info.root, 'etc/default/grub'), encoding='utf-8') as defaults:
        for line in defaults:
            if '=' in line and not line.startswith('#'):
                key, value = line.rstrip('\n').split('=', 1)
                settings[key] = value.strip('"')
    return settings


def test_virtual_console_is_the_kernel_console(tmp_path):
    parameters = grub_defaults(bootstrap_info('buster-console.yml', tmp_path))['GRUB_CMDLINE_LINUX'].split()
    consoles = [parameter for parameter in parameters if parameter.startswith('console=')]
    # The last console= parameter becomes /dev/console, the serial console still gets the kernel messages
    assert consoles[-1] == 'console=tty0'
    assert 'console=ttyS0' in consoles


def test_boot_messages_stay_on_tty1(tmp_path):
    os.makedirs(os.path.join(tmp_path, 'etc/systemd/system'))
    boot.SetSystemdTTYVTDisallocate.run(bootstrap_info('buster-console.yml', tmp_path))
    drop_in = os.path.join(tmp_path, 'etc/systemd/system/getty@tty1.service.d/noclear.conf')
    with open(drop_in, encoding='utf-8') as unit:
        lines = unit.read().splitlines()
    assert lines[lines.index('[Service]') + 1:] == ['TTYVTDisallocate=no']


@pytest.mark.parametrize('name, system, kernel', [
    ('buster-cloudimg.yml', {}, 'linux-image-amd64'),
    ('buster-cloudimg.yml', {'architecture': 'i386'}, 'linux-image-686-pae'),
    ('wheezy.yml', {'architecture': 'i386'}, 'linux-image-686'),
    ('jessie-arm64-virtio.yml', {}, 'linux-image-arm64'),
    ('trixie-openvox.yaml', {'architecture': 'arm64', 'bootloader': 'none'}, 'linux-image-arm64'),
])
def test_kernel_matches_release_and_architecture(tmp_path, name, system, kernel):
    info = bootstrap_info(name, tmp_path, **system)
    packages.DefaultPackages.run(info)
    assert [package.name for package in info.packages.remote()] == [kernel]
