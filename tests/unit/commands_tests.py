import os.path
import subprocess

import pytest

from bootstrapvz.base.bootstrapinfo import BootstrapInformation
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.common import tools
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.commands.tasks import ImageExecuteCommand

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/buster-cloudimg.yml')


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


def test_commands_run_with_root_and_manifest_vars(tmp_path, monkeypatch):
    data = load_data(example)
    data['bootstrapper']['workspace'] = str(tmp_path)
    data['plugins'] = {'commands': {'commands': [['touch', '{root}/var/www/index.html'],
                                                 ['chroot {root} rm -rf /usr/share/locale/[^en]*'],
                                                 ['echo', '{system.release}-{system.architecture}']]}}
    info = BootstrapInformation(manifest=Manifest(path=example, data=data))
    info.root = str(tmp_path / 'root')
    commands = []

    def log_check_call(command, shell):
        commands.append((command, shell))
        return []
    monkeypatch.setattr(tools, 'log_check_call', log_check_call)
    ImageExecuteCommand.run(info)
    assert commands == [(['touch', info.root + '/var/www/index.html'], False),
                        # A command in a single string goes through the shell, so that globbing works
                        (['chroot ' + info.root + ' rm -rf /usr/share/locale/[^en]*'], True),
                        (['echo', 'buster-amd64'], False)]
