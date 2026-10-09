import filecmp
import os.path
import stat
import subprocess

import pytest

from bootstrapvz.base.bootstrapinfo import BootstrapInformation, DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.common.tasks import apt
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.expand_root import tasks

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/buster-cloudimg.yml')

SETTINGS = {'filesystem_type': 'xfs', 'root_device': '/dev/vda', 'root_partition': 2}


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    """Fails the test when a command that the test does not mock would run on the host.
    tools.log_call and tools.log_check_call start their commands through subprocess.Popen, so they fail too.
    They are not replaced by name: a module that imports them while the test runs would keep the
    replacement for the rest of the session.
    """
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {args}'.format(args=args or kwargs))
    monkeypatch.setattr(subprocess, 'Popen', refuse)


def test_expand_root_service_installed_and_enabled(tmp_path, monkeypatch):
    for path in ['usr/local/sbin', 'etc/systemd/system']:
        (tmp_path / path).mkdir(parents=True)
    info = DictClass(root=str(tmp_path), manifest=DictClass(plugins={'expand_root': SETTINGS}))
    commands = []

    def log_check_call(command):
        commands.append(command)
        return []
    monkeypatch.setattr(tasks, 'log_check_call', log_check_call)
    tasks.InstallExpandRootScripts.run(info)

    script = tmp_path / 'usr/local/sbin/expand-root'
    assert filecmp.cmp(script, os.path.join(tasks.ASSETS_DIR, 'expand-root'), shallow=False)
    assert stat.S_IMODE(script.stat().st_mode) == 0o750
    with open(os.path.join(tasks.ASSETS_DIR, 'expand-root.service'), encoding='utf-8') as handle:
        unit = handle.read()
    assert (tmp_path / 'etc/systemd/system/expand-root.service').read_text(encoding='utf-8') == unit.replace(
        'ExecStart=/usr/local/sbin/expand-root DEVICE PARTITION FILESYSTEM',
        'ExecStart=/usr/local/sbin/expand-root /dev/vda 2 xfs')
    assert commands == [['chroot', str(tmp_path), 'systemctl', 'enable', 'expand-root.service']]


@pytest.mark.parametrize('release, package', [('jessie', 'cloud-guest-utils/jessie-backports'),
                                              ('stretch', 'cloud-guest-utils'),
                                              ('trixie', 'cloud-guest-utils')])
def test_growpart_from_backports_on_jessie(tmp_path, release, package):
    data = load_data(example)
    data['bootstrapper']['workspace'] = str(tmp_path)
    data['system']['release'] = release
    data['plugins'] = {'expand_root': SETTINGS}
    info = BootstrapInformation(manifest=Manifest(path=example, data=data))
    # The source that InstallGrowpart installs from on jessie, the ec2, gce and azure providers add it
    apt.AddBackports.run(info)
    tasks.InstallGrowpart.run(info)
    assert [str(package) for package in info.packages.install] == [package]
