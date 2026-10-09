import os.path
import stat
import subprocess
import pytest
from bootstrapvz.base.bootstrapinfo import BootstrapInformation, DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common import tools
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.google_cloud_repo import tasks

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/official/gce/buster.yml')

KEY_URL = 'https://packages.cloud.google.com/apt/doc/apt-key.gpg'
ARMORED_KEY = b'-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nmQENBFUd6rIBCAD6mhKRHDn3UrCeLDp7\n-----END PGP PUBLIC KEY BLOCK-----\n'
BINARY_KEY = b'\x99\x01\x0d\x04\x55\x1d\xea\xb2\x01\x08\x00\xfa\x9a\x12\x91\x1c'


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    """Fails the test instead of running a command that the test did not mock"""
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {args}'.format(args=args or kwargs))
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)


@pytest.fixture(name='root')
def fixture_root(tmp_path):
    root = tmp_path / 'root'
    (root / 'etc/apt/trusted.gpg.d').mkdir(parents=True)
    return root


def gce_manifest(settings):
    data = load_data(example)
    data['plugins']['google_cloud_repo'] = settings
    return Manifest(path=example, data=data)


def image_files(root):
    return sorted(path.relative_to(root).as_posix() for path in root.rglob('*') if not path.is_dir())


@pytest.mark.parametrize('settings, expected', [
    ({}, [tasks.AddGoogleCloudRepoKey]),
    # Without the keyring package, nothing would replace the bootstrap key
    ({'cleanup_bootstrap_key': True}, [tasks.AddGoogleCloudRepoKey]),
    ({'enable_keyring_repo': True}, [tasks.AddGoogleCloudRepoKey,
                                     tasks.AddGoogleCloudRepoKeyringRepo,
                                     tasks.InstallGoogleCloudRepoKeyringPackage]),
    ({'enable_keyring_repo': True, 'cleanup_bootstrap_key': True}, [tasks.AddGoogleCloudRepoKey,
                                                                    tasks.AddGoogleCloudRepoKeyringRepo,
                                                                    tasks.InstallGoogleCloudRepoKeyringPackage,
                                                                    tasks.CleanupBootstrapRepoKey]),
], ids=['default', 'cleanup only', 'keyring repo', 'keyring repo and cleanup'])
def test_resolved_tasks(settings, expected):
    resolved = load_tasks('resolve_tasks', gce_manifest(settings))
    assert sorted(task.__name__ for task in resolved if task.__module__ == tasks.__name__) == \
        sorted(task.__name__ for task in expected)


@pytest.mark.parametrize('key, name', [(ARMORED_KEY, 'google-cloud-bootstrap.asc'),
                                       (BINARY_KEY, 'google-cloud-bootstrap.gpg')],
                         ids=['armored', 'binary'])
def test_downloaded_key_is_installed_where_apt_reads_it(root, monkeypatch, key, name):
    commands = []

    def wget(command):
        commands.append(command)
        # Write the key where wget is told to, with the mode a restrictive umask gives it
        destination = command[-1]
        with open(destination, 'wb') as key_file:
            key_file.write(key)
        os.chmod(destination, 0o600)
        return []
    monkeypatch.setattr(tasks, 'log_check_call', wget)
    tasks.AddGoogleCloudRepoKey.run(DictClass(root=str(root)))
    assert [command[:2] for command in commands] == [['wget', KEY_URL]]
    key_path = root / 'etc/apt/trusted.gpg.d' / name
    assert key_path.read_bytes() == key
    # apt verifies signatures as the _apt user, which must be able to read the key
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o644
    # The download is not left behind in the image
    assert image_files(root) == ['etc/apt/trusted.gpg.d/' + name]


def test_keyring_package_is_installed_from_its_repository():
    info = BootstrapInformation(gce_manifest({'enable_keyring_repo': True}))
    tasks.AddGoogleCloudRepoKeyringRepo.run(info)
    tasks.InstallGoogleCloudRepoKeyringPackage.run(info)
    assert [str(source) for source in info.source_lists.sources['google-cloud']] == \
        ['deb http://packages.cloud.google.com/apt google-cloud-packages-archive-keyring-buster main']
    assert [str(package) for package in info.packages.install] == ['google-cloud-packages-archive-keyring']


@pytest.mark.parametrize('extension', ['.asc', '.gpg'])
def test_cleanup_removes_only_the_bootstrap_key(root, extension):
    keys = root / 'etc/apt/trusted.gpg.d'
    for name in ['google-cloud-bootstrap' + extension,
                 'google-cloud-packages-archive-keyring.gpg',
                 'debian-archive-buster-stable.gpg']:
        (keys / name).write_bytes(b'key')
    tasks.CleanupBootstrapRepoKey.run(DictClass(root=str(root)))
    assert image_files(root) == ['etc/apt/trusted.gpg.d/debian-archive-buster-stable.gpg',
                                 'etc/apt/trusted.gpg.d/google-cloud-packages-archive-keyring.gpg']
