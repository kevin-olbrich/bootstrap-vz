import os.path
import subprocess
import pytest
from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common import tools
from bootstrapvz.common.tasks import ssh
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.root_password.tasks import SetRootPassword

# The GCE provider disables SSH password authentication
example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/official/gce/buster.yml')


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    """Fails the test instead of running a command that the test did not mock"""
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {args}'.format(args=args or kwargs))
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)


def gce_manifest(settings=None):
    data = load_data(example)
    if settings is not None:
        data['plugins']['root_password'] = settings
    return Manifest(path=example, data=data)


def test_root_can_log_in_over_ssh_with_the_password():
    assert ssh.DisableSSHPasswordAuthentication in load_tasks('resolve_tasks', gce_manifest())
    resolved = load_tasks('resolve_tasks', gce_manifest({'password': 's3cr3t'}))
    assert SetRootPassword in resolved
    assert ssh.EnableRootLogin in resolved
    assert ssh.DisableSSHPasswordAuthentication not in resolved


@pytest.mark.parametrize('settings, arguments, stdin', [
    ({'password': 's3cr3t'}, [], 'root:s3cr3t'),
    ({'password-crypted': '$6$salt$hash'}, ['--encrypted'], 'root:$6$salt$hash'),
], ids=['password', 'password-crypted'])
def test_password_is_set_with_chpasswd_in_the_image(tmp_path, monkeypatch, settings, arguments, stdin):
    calls = []

    def log_check_call(command, stdin=None, **kwargs):
        calls.append((command, stdin))
        return []
    monkeypatch.setattr(tools, 'log_check_call', log_check_call)
    SetRootPassword.run(DictClass(root=str(tmp_path), manifest=gce_manifest(settings)))
    assert calls == [(['chroot', str(tmp_path), '/usr/sbin/chpasswd'] + arguments, stdin)]
