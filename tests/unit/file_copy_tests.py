import os.path
import subprocess

import pytest

from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.common import tools
from bootstrapvz.common.exceptions import ManifestError
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.file_copy import tasks

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


def record_commands(monkeypatch):
    """Records the commands that the tasks run. Call it after the manifest is loaded, see no_external_commands."""
    commands = []

    def log_check_call(command):
        commands.append(command)
        return []
    monkeypatch.setattr(tools, 'log_check_call', log_check_call)
    return commands


def file_copy_info(tmp_path, settings):
    data = load_data(example)
    data['plugins'] = {'file_copy': settings}
    # The manifest lives in tmp_path, so that the source paths resolve there
    manifest = Manifest(path=str(tmp_path / 'manifest.yml'), data=data)
    root = tmp_path / 'root'
    root.mkdir()
    return DictClass(manifest=manifest, root=str(root))


def test_directories_created_in_image(tmp_path, monkeypatch):
    info = file_copy_info(tmp_path, {'files': [{'src': 'motd', 'dst': '/etc/motd'}],
                                     'mkdirs': [{'dir': '/srv/www', 'permissions': '0750',
                                                 'owner': 'www-data', 'group': 'www-data'},
                                                {'dir': '/var/cache/app'}]})
    commands = record_commands(monkeypatch)
    tasks.MkdirCommand.run(info)
    # The commands run in the image, where the owner and group exist
    chroot = ['chroot', info.root]
    assert commands == [chroot + ['mkdir', '-p', '/srv/www'],
                        chroot + ['chmod', '0750', '/srv/www'],
                        chroot + ['chown', 'www-data', '/srv/www'],
                        chroot + ['chgrp', 'www-data', '/srv/www'],
                        chroot + ['mkdir', '-p', '/var/cache/app']]


def test_files_and_directories_copied_into_image(tmp_path, monkeypatch):
    (tmp_path / 'files/app/conf.d').mkdir(parents=True)
    (tmp_path / 'files/motd').write_text('Welcome\n', encoding='utf-8')
    (tmp_path / 'files/app/conf.d/app.conf').write_text('port = 8080\n', encoding='utf-8')
    # The source paths are relative to the manifest
    info = file_copy_info(tmp_path, {'files': [{'src': 'files/motd', 'dst': '/etc/motd',
                                                'permissions': '0644', 'owner': 'root', 'group': 'adm'},
                                               {'src': 'files/app', 'dst': '/opt/app'}]})
    root = tmp_path / 'root'
    (root / 'etc').mkdir()
    (root / 'opt').mkdir()
    commands = record_commands(monkeypatch)
    tasks.FileCopyCommand.run(info)
    assert (root / 'etc/motd').read_text(encoding='utf-8') == 'Welcome\n'
    assert (root / 'opt/app/conf.d/app.conf').read_text(encoding='utf-8') == 'port = 8080\n'
    chroot = ['chroot', info.root]
    assert commands == [chroot + ['chmod', '0644', '/etc/motd'],
                        chroot + ['chown', 'root', '/etc/motd'],
                        chroot + ['chgrp', 'adm', '/etc/motd']]


def test_missing_source_rejected(tmp_path):
    (tmp_path / 'motd').write_text('Welcome\n', encoding='utf-8')
    info = file_copy_info(tmp_path, {'files': [{'src': 'motd', 'dst': '/etc/motd'},
                                               {'src': 'issue', 'dst': '/etc/issue'}]})
    with pytest.raises(ManifestError) as excinfo:
        tasks.ValidateFiles.run(info)
    assert list(excinfo.value.data_path) == ['plugins', 'file_copy', 'files', 1]
    (tmp_path / 'issue').write_text('Debian\n', encoding='utf-8')
    tasks.ValidateFiles.run(info)
