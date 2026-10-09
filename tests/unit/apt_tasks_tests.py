import os.path
import stat
import subprocess
import tempfile
from subprocess import CalledProcessError

import pytest

from bootstrapvz.base.bootstrapinfo import BootstrapInformation, DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.pkg.exceptions import PackageError
from bootstrapvz.common import tools
from bootstrapvz.common.exceptions import ManifestError
from bootstrapvz.common.tasks import apt, packages
from bootstrapvz.common.tools import load_data

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/buster-cloudimg.yml')

# The security archive moved from <release>/updates to <release>-security with bullseye
SECURITY_ARCHIVES = {
    'wheezy': 'http://security.debian.org/ wheezy/updates',
    'jessie': 'http://security.debian.org/ jessie/updates',
    'buster': 'http://security.debian.org/ buster/updates',
    'bullseye': 'http://security.debian.org/debian-security bullseye-security',
    'bookworm': 'http://security.debian.org/debian-security bookworm-security',
    'trixie': 'http://security.debian.org/debian-security trixie-security',
}


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {command}'.format(command=args or kwargs))
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)


@pytest.fixture(name='root')
def fixture_root(tmp_path):
    """An image root with the directories that debootstrap creates and the apt tasks write into"""
    root = tmp_path / 'root'
    for path in ['etc/apt/sources.list.d', 'etc/apt/preferences.d', 'etc/apt/apt.conf.d',
                 'etc/apt/trusted.gpg.d', 'var/lib/apt/lists/partial', 'boot', 'tmp']:
        (root / path).mkdir(parents=True)
    return root


def bootstrap_info(root, release, package_settings=None, path=example):
    data = load_data(example)
    data['system']['release'] = release
    data['packages'] = package_settings or {}
    del data['plugins']
    info = BootstrapInformation(manifest=Manifest(path=path, data=data))
    info.root = str(root)
    return info


def record_commands(monkeypatch, module, output=None):
    """Replaces the command runner of a task module and returns the list of commands it is given"""
    commands = []

    def log_check_call(command, **kwargs):
        commands.append(command)
        return output or []
    monkeypatch.setattr(module, 'log_check_call', log_check_call)
    return commands


def read(path):
    return path.read_text(encoding='utf-8')


def write_sources(info, *tasks):
    for task in tasks:
        task.run(info)
    apt.WriteSources.run(info)


@pytest.mark.parametrize('release', list(SECURITY_ARCHIVES))
def test_default_sources(root, release):
    write_sources(bootstrap_info(root, release), apt.AddDefaultSources)
    assert read(root / 'etc/apt/sources.list') == (
        'deb http://deb.debian.org/debian/ {release} main\n'
        'deb {security} main\n'
        'deb http://deb.debian.org/debian/ {release}-updates main\n'
    ).format(release=release, security=SECURITY_ARCHIVES[release])
    assert not os.listdir(root / 'etc/apt/sources.list.d')


def test_default_sources_for_sid(root):
    # sid has neither security support nor updates
    write_sources(bootstrap_info(root, 'sid'), apt.AddDefaultSources)
    assert read(root / 'etc/apt/sources.list') == 'deb http://deb.debian.org/debian/ sid main\n'


@pytest.mark.parametrize('release', ['jessie', 'bookworm'])
def test_default_sources_follow_the_package_settings(root, release):
    settings = {'mirror': 'http://mirror.example.org/debian/',
                'components': ['main', 'contrib'],
                'include-source-type': True}
    write_sources(bootstrap_info(root, release, settings), apt.AddDefaultSources)
    archives = ['http://mirror.example.org/debian/ ' + release,
                SECURITY_ARCHIVES[release],
                'http://mirror.example.org/debian/ {release}-updates'.format(release=release)]
    assert read(root / 'etc/apt/sources.list') == ''.join(
        '{type} {archive} main contrib\n'.format(type=source_type, archive=archive)
        for archive in archives for source_type in ['deb', 'deb-src'])


def test_manifest_sources(root):
    sources = {'main': ['deb http://deb.example.org/debian {system.release} main'],
               'openvox': ['deb https://apt.voxpupuli.org debian12 openvox8']}
    write_sources(bootstrap_info(root, 'bookworm', {'sources': sources}),
                  apt.AddManifestSources, apt.AddDefaultSources)
    sources_list = read(root / 'etc/apt/sources.list').splitlines()
    assert 'deb http://deb.example.org/debian bookworm main' in sources_list
    assert 'deb http://deb.debian.org/debian/ bookworm main' in sources_list
    assert os.listdir(root / 'etc/apt/sources.list.d') == ['openvox.list']
    openvox_list = read(root / 'etc/apt/sources.list.d/openvox.list')
    assert openvox_list == 'deb https://apt.voxpupuli.org debian12 openvox8\n'


@pytest.mark.parametrize('release', ['wheezy', 'bookworm', 'trixie', 'stable'])
def test_backports(root, release):
    write_sources(bootstrap_info(root, release), apt.AddDefaultSources, apt.AddBackports)
    assert read(root / 'etc/apt/sources.list.d/backports.list') == (
        'deb http://deb.debian.org/debian/ {release}-backports main\n'
        'deb-src http://deb.debian.org/debian/ {release}-backports main\n'
    ).format(release=release)


@pytest.mark.parametrize('release', ['forky', 'testing', 'sid', 'unstable'])
def test_no_backports_for_unreleased_releases(root, release):
    write_sources(bootstrap_info(root, release), apt.AddDefaultSources, apt.AddBackports)
    assert not os.listdir(root / 'etc/apt/sources.list.d')


def test_manifest_backports_source_replaces_the_default(root):
    sources = {'backports': ['deb http://mirror.example.org/debian {system.release}-backports main contrib']}
    write_sources(bootstrap_info(root, 'bookworm', {'sources': sources}),
                  apt.AddManifestSources, apt.AddDefaultSources, apt.AddBackports)
    assert read(root / 'etc/apt/sources.list.d/backports.list') == (
        'deb http://mirror.example.org/debian bookworm-backports main contrib\n')


def test_preferences(root):
    preferences = {'main': [{'package': '*', 'pin': 'release o=Debian, n=bookworm', 'pin-priority': 800},
                            {'package': '*', 'pin': 'release a=bookworm-backports', 'pin-priority': 760}],
                   'openvox': [{'package': 'openvox-agent', 'pin': 'version 8.*', 'pin-priority': 840}]}
    info = bootstrap_info(root, 'bookworm', {'preferences': preferences})
    apt.AddManifestPreferences.run(info)
    apt.WritePreferences.run(info)
    # apt_preferences(5) separates the records with an empty line
    assert read(root / 'etc/apt/preferences') == (
        'Package: *\nPin: release o=Debian, n=bookworm\nPin-Priority: 800\n\n'
        'Package: *\nPin: release a=bookworm-backports\nPin-Priority: 760\n\n')
    assert os.listdir(root / 'etc/apt/preferences.d') == ['openvox']
    assert read(root / 'etc/apt/preferences.d/openvox') == (
        'Package: openvox-agent\nPin: version 8.*\nPin-Priority: 840\n\n')


def test_apt_configuration(root):
    # Loading a manifest with apt.conf.d runs apt-config, so the task gets the settings directly
    settings = {'apt.conf.d': {'main': 'APT::Install-Recommends "false";',
                               '00IPv4': 'Acquire::ForceIPv4 "true";'}}
    apt.WriteConfiguration.run(DictClass(root=str(root), manifest=DictClass(packages=settings)))
    assert read(root / 'etc/apt/apt.conf') == 'APT::Install-Recommends "false";\n'
    assert os.listdir(root / 'etc/apt/apt.conf.d') == ['00IPv4']
    assert read(root / 'etc/apt/apt.conf.d/00IPv4') == 'Acquire::ForceIPv4 "true";\n'


def test_trusted_keys_are_installed_relative_to_the_manifest(tmp_path, root):
    key = tmp_path / 'keys/openvox.gpg'
    key.parent.mkdir()
    key.write_bytes(b'keyring')
    key.chmod(0o600)
    info = bootstrap_info(root, 'bookworm', {'trusted-keys': ['keys/openvox.gpg']},
                          path=str(tmp_path / 'manifest.yml'))
    apt.InstallTrustedKeys.run(info)
    installed = root / 'etc/apt/trusted.gpg.d/openvox.gpg'
    assert installed.read_bytes() == b'keyring'
    # apt reads the keyrings as the unprivileged _apt user
    assert stat.S_IMODE(installed.stat().st_mode) == 0o644


def validate_trusted_keys(monkeypatch, tmp_path, root, key_exists=True, gpg_status=0):
    """Runs ValidateTrustedKeys for a keyring and returns the gpg commands with their home directories"""
    if key_exists:
        (tmp_path / 'openvox.gpg').write_bytes(b'keyring')
    gpg_tmp = tmp_path / 'gpg-tmp'
    gpg_tmp.mkdir()
    monkeypatch.setattr(tempfile, 'tempdir', str(gpg_tmp))
    calls = []

    def log_call(command, **kwargs):
        homedir = command[command.index('--homedir') + 1]
        calls.append((command, homedir, os.path.isdir(homedir)))
        return gpg_status, [], []
    monkeypatch.setattr(tools, 'log_call', log_call)
    info = bootstrap_info(root, 'bookworm', {'trusted-keys': ['openvox.gpg']},
                          path=str(tmp_path / 'manifest.yml'))
    try:
        apt.ValidateTrustedKeys.run(info)
    finally:
        # gpg must not leave its temporary home directory behind
        assert not os.listdir(gpg_tmp)
    return calls


def test_trusted_keys_are_checked_with_gpg(monkeypatch, tmp_path, root):
    [(command, homedir, homedir_existed)] = validate_trusted_keys(monkeypatch, tmp_path, root)
    assert command[0] == 'gpg'
    assert command[command.index('--keyring') + 1] == str(tmp_path / 'openvox.gpg')
    # gpg gets an empty home directory instead of the one of the user on the build host
    assert homedir_existed
    assert os.path.dirname(homedir) == str(tmp_path / 'gpg-tmp')


def test_invalid_trusted_key_is_rejected(monkeypatch, tmp_path, root):
    with pytest.raises(ManifestError, match='Invalid GPG keyring') as excinfo:
        validate_trusted_keys(monkeypatch, tmp_path, root, gpg_status=2)
    assert excinfo.value.data_path == ['packages', 'trusted-keys', 0]


def test_missing_trusted_key_is_rejected(monkeypatch, tmp_path, root):
    with pytest.raises(ManifestError, match='File not found') as excinfo:
        validate_trusted_keys(monkeypatch, tmp_path, root, key_exists=False)
    assert excinfo.value.data_path == ['packages', 'trusted-keys', 0]


@pytest.mark.parametrize('merged_usr', [False, True], ids=['jessie', 'bookworm'])
def test_daemons_do_not_start_during_package_installation(root, merged_usr):
    # debootstrap merges /usr from bookworm on, so sbin is a relative link to usr/sbin there
    (root / 'usr/sbin').mkdir(parents=True)
    if merged_usr:
        (root / 'sbin').symlink_to('usr/sbin')
    else:
        (root / 'sbin').mkdir()
    info = DictClass(root=str(root))
    apt.DisableDaemonAutostart.run(info)
    for path, content in [('usr/sbin/policy-rc.d', '#!/bin/sh\nexit 101'),
                          ('sbin/initctl', '#!/bin/sh\nexit 0')]:
        assert read(root / path) == content
        assert stat.S_IMODE((root / path).stat().st_mode) == 0o755
        assert os.path.realpath(root / path).startswith(str(root) + '/')
    apt.EnableDaemonAutostart.run(info)
    assert not os.listdir(root / 'usr/sbin')
    assert not os.listdir(root / 'sbin')


def test_apt_commands(root, monkeypatch):
    commands = record_commands(monkeypatch, apt)
    info = DictClass(root=str(root))
    for task in [apt.AptUpdate, apt.AptUpgrade, apt.PurgeUnusedPackages, apt.AptClean]:
        task.run(info)
    chroot = ['chroot', str(root)]
    assert commands == [
        chroot + ['apt-get', 'update'],
        chroot + ['apt-get', 'install', '--fix-broken', '--no-install-recommends', '--assume-yes'],
        chroot + ['apt-get', 'upgrade', '--no-install-recommends', '--assume-yes'],
        chroot + ['apt-get', 'autoremove', '--purge', '--assume-yes'],
        chroot + ['apt-get', 'clean'],
    ]


def test_apt_clean_removes_the_package_lists(root, monkeypatch):
    record_commands(monkeypatch, apt)
    lists = root / 'var/lib/apt/lists'
    for name in ['lock', 'deb.debian.org_debian_dists_bookworm_InRelease',
                 'deb.debian.org_debian_dists_bookworm_main_binary-amd64_Packages.lz4']:
        (lists / name).write_text('list\n', encoding='utf-8')
    apt.AptClean.run(DictClass(root=str(root)))
    assert os.listdir(lists) == ['partial']


def test_failed_upgrade_stops_the_build(root, monkeypatch):
    def log_check_call(command, **kwargs):
        raise CalledProcessError(100, ' '.join(command))
    monkeypatch.setattr(apt, 'log_check_call', log_check_call)
    with pytest.raises(CalledProcessError):
        apt.AptUpgrade.run(DictClass(root=str(root)))


def test_packages_are_installed_in_manifest_order(tmp_path, root, monkeypatch):
    debs = tmp_path / 'debs'
    debs.mkdir()
    for name in ['first.deb', 'second.deb', 'last.deb']:
        (debs / name).write_text(name, encoding='utf-8')
    install = [str(debs / 'first.deb'), str(debs / 'second.deb'),
               'vim', 'cloud-init/{system.release}-backports',
               str(debs / 'last.deb')]
    info = bootstrap_info(root, 'bookworm', {'install': install})
    # The installers get a noninteractive frontend whatever the build host sets
    monkeypatch.delenv('DEBIAN_FRONTEND', raising=False)
    calls = []

    def log_check_call(command, env=None):
        # Local packages must be inside the image while dpkg installs them
        local = [read(root / path.lstrip('/')) for path in command[4:] if path.startswith('/tmp/')]
        calls.append((command, env['DEBIAN_FRONTEND'], local))
    monkeypatch.setattr(packages, 'log_check_call', log_check_call)
    for task in [apt.AddDefaultSources, apt.AddBackports,
                 packages.AddManifestPackages, packages.InstallPackages]:
        task.run(info)
    chroot = ['chroot', str(root)]
    assert calls == [
        (chroot + ['dpkg', '--install', '/tmp/first.deb', '/tmp/second.deb'], 'noninteractive',
         ['first.deb', 'second.deb']),
        (chroot + ['apt-get', 'install', '--no-install-recommends', '--assume-yes',
                   'vim', 'cloud-init/bookworm-backports'], 'noninteractive', []),
        (chroot + ['dpkg', '--install', '/tmp/last.deb'], 'noninteractive', ['last.deb']),
    ]
    assert not os.listdir(root / 'tmp')


def test_package_target_without_source_is_rejected(root):
    # testing has no backports, so the build fails before anything is installed
    info = bootstrap_info(root, 'forky', {'install': ['cloud-init/{system.release}-backports']})
    apt.AddDefaultSources.run(info)
    apt.AddBackports.run(info)
    with pytest.raises(PackageError, match='forky-backports was not found'):
        packages.AddManifestPackages.run(info)


def test_standard_packages_from_tasksel(root, monkeypatch):
    commands = record_commands(monkeypatch, packages, output=['bash-completion', 'less'])
    info = bootstrap_info(root, 'bookworm', {'install_standard': True})
    packages.AddTaskselStandardPackages.run(info)
    packages.InstallPackages.run(info)
    chroot = ['chroot', str(root)]
    assert commands == [chroot + ['tasksel', '--task-packages', 'standard'],
                        chroot + ['apt-get', 'install', '--no-install-recommends', '--assume-yes',
                                  'bash-completion', 'less']]


def test_failed_package_installation_stops_the_build(root, monkeypatch):
    def log_check_call(command, **kwargs):
        raise CalledProcessError(100, ' '.join(command))
    monkeypatch.setattr(packages, 'log_check_call', log_check_call)
    info = bootstrap_info(root, 'bookworm')
    info.packages.add('vim')
    with pytest.raises(CalledProcessError):
        packages.InstallPackages.run(info)
