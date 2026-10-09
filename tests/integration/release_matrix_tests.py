import functools
import os.path
import subprocess

import pytest

from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import create_list, get_all_tasks, load_tasks
from bootstrapvz.common import tools
from bootstrapvz.common.exceptions import ManifestError
from bootstrapvz.common.releases import bookworm, get_release, jessie, stretch, trixie
from bootstrapvz.common.tasks import cleanup, filesystem, grub, locale
from bootstrapvz.common.tools import load_data

manifests = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests')

# One manifest per provider and the architectures that the provider has kernels for
# (tasks/packages-kernels.yml). Docker images contain no kernel, so docker takes every architecture.
PROVIDERS = {'azure':      ('examples/azure/jessie.yml', ['amd64', 'i386']),
             'docker':     ('examples/docker/stretch.yml', ['amd64', 'arm64', 'i386']),
             'ec2':        ('official/ec2/ebs-stretch-amd64-hvm.yml', ['amd64', 'i386']),
             'gce':        ('official/gce/buster.yml', ['amd64']),
             'kvm':        ('examples/kvm/buster-cloudimg.yml', ['amd64', 'arm64', 'i386']),
             'oracle':     ('official/oracle/jessie.yml', ['amd64', 'i386']),
             'virtualbox': ('examples/virtualbox/stretch-vagrant.yml', ['amd64', 'i386']),
             }

# Every release from wheezy to sid, and the aliases
RELEASES = ['wheezy', 'jessie', 'stretch', 'buster', 'bullseye', 'bookworm', 'trixie', 'forky', 'duke', 'sid',
            'oldstable', 'stable', 'testing', 'unstable']


def rejection(provider, release, architecture):
    """Returns the error that validation rejects the combination with on purpose, or None if it accepts it"""
    if architecture == 'i386' and get_release(release) >= trixie:
        return 'The i386 architecture is only supported up to Debian bookworm'
    if provider == 'kvm' and architecture == 'arm64' and get_release(release) < jessie:
        return 'The kvm provider has no kernel for the arm64 architecture on Debian wheezy'
    return None


MATRIX = [(provider, release, architecture)
          for provider, (_, architectures) in sorted(PROVIDERS.items())
          for release in RELEASES
          for architecture in architectures]
ACCEPTED = [combination for combination in MATRIX if rejection(*combination) is None]
REJECTED = [combination for combination in MATRIX if rejection(*combination) is not None]


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    # Validating and resolving a manifest must not run anything on the build host
    def refuse(command, *args, **kwargs):
        raise AssertionError('runs a command: {command}'.format(command=command))
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)


def load_manifest(provider, release, architecture):
    path = os.path.join(manifests, PROVIDERS[provider][0])
    data = load_data(path)
    data['system']['release'] = release
    data['system']['architecture'] = architecture
    if architecture == 'arm64':
        # The validation only allows grub and extlinux on i386 and amd64
        data['system']['bootloader'] = 'none'
    return Manifest(path=path, data=data)


@functools.cache
def order(tasks):
    # Orders the tasks like TaskList.run does. Ordering is the slow part and many combinations
    # resolve the same tasks, so each set is ordered only once.
    return create_list(set(tasks), set(get_all_tasks([])))


RELEASE_TASKS = {filesystem.CopyMountTable, filesystem.RemoveMountTable,
                 locale.SetLocalTimeCopy, locale.SetLocalTimeLink,
                 cleanup.ClearMachineId,
                 grub.InstallGrub_1_99, grub.InstallGrub_2, grub.DisablePNIN}


def release_tasks(release, bootloader):
    """Returns the tasks of RELEASE_TASKS that the release rules in bootstrapvz/common/task_groups.py pick"""
    tasks = {locale.SetLocalTimeLink if release > jessie else locale.SetLocalTimeCopy}
    if release < bookworm:
        tasks.update([filesystem.CopyMountTable, filesystem.RemoveMountTable])
    if release >= jessie:
        tasks.add(cleanup.ClearMachineId)
    if bootloader == 'grub':
        tasks.add(grub.InstallGrub_2 if release >= jessie else grub.InstallGrub_1_99)
        if release >= stretch:
            tasks.add(grub.DisablePNIN)
    return tasks


@pytest.mark.parametrize('provider,release,architecture', ACCEPTED, ids=['-'.join(c) for c in ACCEPTED])
def test_tasks_are_resolved_and_ordered(provider, release, architecture):
    manifest = load_manifest(provider, release, architecture)
    tasks = load_tasks('resolve_tasks', manifest)
    assert set(order(frozenset(tasks))) == tasks
    assert tasks & RELEASE_TASKS == release_tasks(manifest.release, manifest.system['bootloader'])


@pytest.mark.parametrize('provider,release,architecture', REJECTED, ids=['-'.join(c) for c in REJECTED])
def test_unsupported_combination_is_rejected(provider, release, architecture):
    with pytest.raises(ManifestError) as excinfo:
        load_manifest(provider, release, architecture)
    assert excinfo.value.message == rejection(provider, release, architecture)
    assert excinfo.value.data_path == ['system', 'architecture']
