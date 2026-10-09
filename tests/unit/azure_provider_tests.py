import os.path
import subprocess

import pytest

from bootstrapvz.base.bootstrapinfo import BootstrapInformation
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import create_list, get_all_tasks, load_tasks
from bootstrapvz.common import tools
from bootstrapvz.common.tasks import grub
from bootstrapvz.common.tools import load_data
from bootstrapvz.providers.azure.tasks import boot, packages

examples = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests/examples/azure')


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {args}'.format(args=args))
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)


def bootstrap_info(root, name='jessie.yml', **system):
    path = os.path.join(examples, name)
    data = load_data(path)
    data['system'].update(system)
    info = BootstrapInformation(Manifest(path=path, data=data))
    info.root = str(root)
    return info


def kernel_parameters(info):
    """Runs the tasks that fill the grub configuration in tasklist order
    and returns the kernel parameters from /etc/default/grub"""
    manifest = info.manifest
    all_tasks = set(get_all_tasks([manifest.modules['provider']] + manifest.modules['plugins']))
    os.makedirs(os.path.join(info.root, 'etc/default'))
    for task in create_list(load_tasks('resolve_tasks', manifest), all_tasks):
        if task in (grub.InitGrubConfig, grub.WriteGrubConfig) or grub.WriteGrubConfig in task.successors:
            task.run(info)
    with open(os.path.join(info.root, 'etc/default/grub'), encoding='utf-8') as defaults:
        for line in defaults:
            if line.startswith('GRUB_CMDLINE_LINUX='):
                return line.rstrip('\n').split('=', 1)[1].strip('"').split()
    return []


def test_kernel_logs_to_the_serial_console(tmp_path):
    parameters = kernel_parameters(bootstrap_info(tmp_path))
    consoles = [parameter for parameter in parameters if parameter.startswith('console=')]
    # The last console= parameter becomes /dev/console, Azure shows ttyS0 in the boot diagnostics
    assert consoles == ['console=tty0', 'console=ttyS0,115200n8']
    assert {'earlyprintk=ttyS0,115200', 'rootdelay=300'} <= set(parameters)


@pytest.mark.parametrize('release', ['jessie', 'bookworm'])
def test_default_packages(tmp_path, release):
    info = bootstrap_info(tmp_path, release=release)
    packages.DefaultPackages.run(info)
    names = {package.name for package in info.packages.remote()}
    assert {'openssl', 'sudo', 'parted'} <= names
    assert [name for name in names if name.startswith('linux-image-')] == ['linux-image-amd64']


def test_udev_patch_targets_the_image(tmp_path, monkeypatch):
    commands = []

    def log_check_call(command):
        commands.append(command)
        return []
    monkeypatch.setattr(tools, 'log_check_call', log_check_call)
    boot.PatchUdev.run(bootstrap_info(tmp_path, 'wheezy.yml'))
    [command] = commands
    assert command[:2] == ['patch', '--no-backup-if-mismatch']
    assert command[2] == os.path.join(tmp_path, 'usr/share/initramfs-tools/scripts/init-top/udev')
    # Azure boots with rootdelay=300, and the udev script of old releases sleeps that long on every boot
    with open(command[3], encoding='utf-8') as diff:
        assert '-\tsleep $ROOTDELAY' in diff.read().splitlines()
