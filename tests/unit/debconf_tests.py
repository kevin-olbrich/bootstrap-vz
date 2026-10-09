import subprocess

import pytest

from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.common import tools
from bootstrapvz.plugins import debconf
from bootstrapvz.plugins.debconf import tasks

SELECTIONS = ('d-i pkgsel/install-language-support boolean false\n'
              'popularity-contest popularity-contest/participate boolean false')


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


def recorder(commands):
    def log_check_call(command, stdin, log_stdin):
        commands.append((command, stdin))
        return []
    return log_check_call


def test_selections_checked_on_validation(monkeypatch):
    commands = []
    monkeypatch.setattr(tools, 'log_check_call', recorder(commands))
    debconf.validate_manifest({'plugins': {'debconf': SELECTIONS}}, lambda data, schema_path: None, None)
    # The syntax is checked on the host, before anything is built
    assert commands == [(['debconf-set-selections', '--checkonly'], SELECTIONS)]


def test_selections_set_in_image(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(tasks, 'log_check_call', recorder(commands))
    tasks.DebconfSetSelections.run(DictClass(root=str(tmp_path), manifest=DictClass(plugins={'debconf': SELECTIONS})))
    assert commands == [(['chroot', str(tmp_path), 'debconf-set-selections'], SELECTIONS)]
