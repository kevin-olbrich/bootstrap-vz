import os.path
import re
import subprocess

import pytest

from bootstrapvz.base.bootstrapinfo import BootstrapInformation
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import create_list, get_all_tasks, load_tasks
from bootstrapvz.common import tools
from bootstrapvz.common.exceptions import TaskError
from bootstrapvz.common.tasks import grub
from bootstrapvz.common.tools import load_data
from bootstrapvz.providers.virtualbox.tasks import guest_additions, packages

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/virtualbox/wheezy.yml')


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {args}'.format(args=args))
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)


def bootstrap_info(root, path=example, provider=None, **system):
    data = load_data(example)
    data['provider'].update(provider or {})
    data['system'].update(system)
    info = BootstrapInformation(Manifest(path=path, data=data))
    info.root = str(root)
    return info


def package_names(info):
    return [package.name for package in info.packages.remote()]


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


def test_default_boot_entry_shows_messages_in_the_vm_window(tmp_path):
    settings = grub_defaults(bootstrap_info(tmp_path))
    # grub puts GRUB_CMDLINE_LINUX_DEFAULT after GRUB_CMDLINE_LINUX, so the virtual console
    # is the last console= parameter and becomes /dev/console
    assert settings['GRUB_CMDLINE_LINUX_DEFAULT'].split()[-1] == 'console=tty0'
    assert 'console=ttyS0' in settings['GRUB_CMDLINE_LINUX'].split()


def test_guest_additions_image_is_found_relative_to_the_manifest(tmp_path):
    os.mkdir(os.path.join(tmp_path, 'iso'))
    with open(os.path.join(tmp_path, 'iso/VBoxGuestAdditions.iso'), 'wb'):
        pass
    manifest_path = os.path.join(tmp_path, 'manifest.yml')
    info = bootstrap_info(tmp_path, manifest_path, {'guest_additions': 'iso/VBoxGuestAdditions.iso'})
    guest_additions.CheckGuestAdditionsPath.run(info)


def test_missing_guest_additions_image_fails_validation(tmp_path):
    manifest_path = os.path.join(tmp_path, 'manifest.yml')
    info = bootstrap_info(tmp_path, manifest_path, {'guest_additions': 'iso/VBoxGuestAdditions.iso'})
    missing = os.path.join(tmp_path, 'iso/VBoxGuestAdditions.iso')
    with pytest.raises(TaskError, match=re.escape(missing)):
        guest_additions.CheckGuestAdditionsPath.run(info)


@pytest.mark.parametrize('architecture, headers', [('amd64', 'linux-headers-amd64'),
                                                   ('i386', 'linux-headers-686-pae')])
def test_guest_additions_get_their_build_dependencies(tmp_path, architecture, headers):
    info = bootstrap_info(tmp_path, architecture=architecture)
    guest_additions.AddGuestAdditionsPackages.run(info)
    assert sorted(package_names(info)) == sorted(['bzip2', 'build-essential', 'dkms', headers])


@pytest.mark.parametrize('release, architecture, kernel', [('wheezy', 'amd64', 'linux-image-amd64'),
                                                           ('wheezy', 'i386', 'linux-image-686'),
                                                           ('bookworm', 'i386', 'linux-image-686-pae'),
                                                           ('trixie', 'amd64', 'linux-image-amd64')])
def test_kernel_matches_release_and_architecture(tmp_path, release, architecture, kernel):
    info = bootstrap_info(tmp_path, release=release, architecture=architecture)
    packages.DefaultPackages.run(info)
    assert package_names(info) == [kernel]
