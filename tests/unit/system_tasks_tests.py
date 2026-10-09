import os.path
import stat
import subprocess
from pathlib import Path
from subprocess import CalledProcessError

import pytest

from bootstrapvz.base.bootstrapinfo import BootstrapInformation, DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.common import phases, task_groups, tools
from bootstrapvz.common.releases import get_release, jessie
from bootstrapvz.common.tasks import assets, cleanup, dpkg, folder, initd, locale, network, security, ssh
from bootstrapvz.common.tasks import workspace
from bootstrapvz.common.tools import load_data

docker_example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                              '../../manifests/examples/docker/stretch.yml')

# The settings in the sshd_config that openssh-server installs, which the ssh tasks edit,
# and the comments that mention them
SSHD_CONFIGS = {
    'wheezy': ('# Package generated configuration file\n'
               'Port 22\n'
               '# Authentication:\n'
               'LoginGraceTime 120\n'
               'PermitRootLogin yes\n'
               'StrictModes yes\n'
               '# Change to no to disable tunnelled clear text passwords\n'
               '#PasswordAuthentication yes\n'
               '# PasswordAuthentication.  Depending on your PAM configuration,\n'
               '# the setting of "PermitRootLogin without-password".\n'
               'UsePAM yes\n'),
    'jessie': ('# Package generated configuration file\n'
               'Port 22\n'
               '# Authentication:\n'
               'LoginGraceTime 120\n'
               'PermitRootLogin without-password\n'
               'StrictModes yes\n'
               '# Change to no to disable tunnelled clear text passwords\n'
               '#PasswordAuthentication yes\n'
               '# PasswordAuthentication.  Depending on your PAM configuration,\n'
               '# the setting of "PermitRootLogin without-password".\n'
               'UsePAM yes\n'),
    'bookworm': ('Include /etc/ssh/sshd_config.d/*.conf\n'
                 '# Authentication:\n'
                 '#LoginGraceTime 2m\n'
                 '#PermitRootLogin prohibit-password\n'
                 '#StrictModes yes\n'
                 '# To disable tunneled clear text passwords, change to no here!\n'
                 '#PasswordAuthentication yes\n'
                 '#PermitEmptyPasswords no\n'
                 '# PasswordAuthentication.  Depending on your PAM configuration,\n'
                 '# the setting of "PermitRootLogin prohibit-password".\n'
                 'UsePAM yes\n'
                 '#UseDNS no\n'
                 'Subsystem\tsftp\t/usr/lib/openssh/sftp-server\n'),
}
ROOT_LOGIN = {'wheezy': 'PermitRootLogin yes',
              'jessie': 'PermitRootLogin without-password',
              'bookworm': '#PermitRootLogin prohibit-password'}
# The SSH host key types of a release: wheezy has no ed25519 keys yet and bookworm no dsa keys anymore
HOST_KEY_TYPES = {'wheezy': ['dsa', 'rsa', 'ecdsa'],
                  'jessie': ['dsa', 'rsa', 'ecdsa', 'ed25519'],
                  'bookworm': ['rsa', 'ecdsa', 'ed25519']}
# The machine ids that a bootstrapped image of each release has
MACHINE_IDS = {'wheezy': [],
               'jessie': ['etc/machine-id', 'var/lib/dbus/machine-id'],
               'bookworm': ['etc/machine-id'],
               'trixie': ['etc/machine-id']}


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {command}'.format(command=args or kwargs))
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)


@pytest.fixture(name='root')
def fixture_root(tmp_path):
    root = tmp_path / 'root'
    (root / 'etc').mkdir(parents=True)
    return root


def read(path):
    return path.read_text(encoding='utf-8')


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def record_commands(monkeypatch, *modules):
    """Replaces the command runner of the task modules and returns the list of commands it is given"""
    commands = []

    def log_check_call(command, **kwargs):
        commands.append(command)
        return []
    for module in modules:
        monkeypatch.setattr(module, 'log_check_call', log_check_call)
    return commands


def release_info(root, release, **settings):
    return DictClass(root=str(root), manifest=DictClass(release=get_release(release), **settings))


@pytest.mark.parametrize('locale_name', ['en_US', 'de_DE'])
def test_generate_locale(root, monkeypatch, locale_name):
    locale_gen = ('# This file lists locales that you wish to have built.\n'
                  '#\n'
                  '\n'
                  '# de_DE.UTF-8 UTF-8\n'
                  '# de_DE ISO-8859-1\n'
                  '# en_US.UTF-8 UTF-8\n'
                  '# en_US ISO-8859-1\n')
    write(root / 'etc/locale.gen', locale_gen)
    commands = record_commands(monkeypatch, tools)
    info = DictClass(root=str(root), manifest=DictClass(system={'locale': locale_name, 'charmap': 'UTF-8'}))
    locale.GenerateLocale.run(info)
    selected = locale_name + '.UTF-8 UTF-8\n'
    assert read(root / 'etc/locale.gen') == locale_gen.replace('# ' + selected, selected)
    assert commands == [['chroot', str(root), 'locale-gen'],
                        ['chroot', str(root), 'update-locale', 'LANG=' + locale_name + '.UTF-8']]


@pytest.mark.parametrize('release', ['wheezy', 'jessie', 'bookworm', 'trixie'])
def test_timezone(root, release):
    for name in ['Etc/UTC', 'Europe/Berlin']:
        write(root / 'usr/share/zoneinfo' / name, name + ' zoneinfo')
    write(root / 'etc/timezone', 'Etc/UTC\n')
    # Images up to jessie get a copy of the zoneinfo file, newer images a link to it
    copy = get_release(release) <= jessie
    if copy:
        write(root / 'etc/localtime', 'Etc/UTC zoneinfo')
    else:
        (root / 'etc/localtime').symlink_to('/usr/share/zoneinfo/Etc/UTC')
    info = release_info(root, release, system={'timezone': 'Europe/Berlin'})
    for task in task_groups.get_locale_group(info.manifest):
        if task.phase is phases.system_modification:
            task.run(info)
    assert read(root / 'etc/timezone').splitlines() == ['Europe/Berlin']
    if copy:
        assert not os.path.islink(root / 'etc/localtime')
        assert read(root / 'etc/localtime') == 'Europe/Berlin zoneinfo'
    else:
        assert os.readlink(root / 'etc/localtime') == '/usr/share/zoneinfo/Europe/Berlin'


@pytest.mark.parametrize('release', ['wheezy', 'jessie', 'bookworm', 'trixie', 'sid'])
def test_network_interfaces(root, release):
    installed = ('# interfaces(5) file used by ifup(8) and ifdown(8)\n'
                 'source-directory /etc/network/interfaces.d\n')
    write(root / 'etc/network/interfaces', installed)
    network.ConfigureNetworkIF.run(release_info(root, release))
    loopback = 'auto lo\niface lo inet loopback\n' if release == 'jessie' else ''
    eth0 = 'auto eth0\niface eth0 inet dhcp\n'
    assert read(root / 'etc/network/interfaces') == installed + loopback + eth0 + '\n'


@pytest.mark.parametrize('existing', [True, False], ids=['copied', 'missing'])
def test_host_network_settings_are_removed(root, existing):
    # debootstrap copies resolv.conf and hostname from the build host
    if existing:
        write(root / 'etc/resolv.conf', 'nameserver 192.0.2.53\n')
        write(root / 'etc/hostname', 'buildhost\n')
    write(root / 'etc/hosts', '127.0.0.1\tlocalhost\n')
    info = DictClass(root=str(root))
    network.RemoveDNSInfo.run(info)
    network.RemoveHostname.run(info)
    assert os.listdir(root / 'etc') == ['hosts']


@pytest.mark.parametrize('release', ['wheezy', 'jessie', 'bookworm'])
def test_ssh_password_authentication_is_disabled(root, release):
    write(root / 'etc/ssh/sshd_config', SSHD_CONFIGS[release])
    ssh.DisableSSHPasswordAuthentication.run(DictClass(root=str(root)))
    assert read(root / 'etc/ssh/sshd_config') == SSHD_CONFIGS[release].replace(
        '#PasswordAuthentication yes\n', 'PasswordAuthentication no\n')


@pytest.mark.parametrize('release', ['wheezy', 'jessie', 'bookworm'])
@pytest.mark.parametrize('task, setting', [(ssh.EnableRootLogin, 'yes'), (ssh.DisableRootLogin, 'no')])
def test_ssh_root_login(root, release, task, setting):
    write(root / 'etc/ssh/sshd_config', SSHD_CONFIGS[release])
    task.run(DictClass(root=str(root)))
    assert read(root / 'etc/ssh/sshd_config') == SSHD_CONFIGS[release].replace(
        ROOT_LOGIN[release] + '\n', 'PermitRootLogin ' + setting + '\n')


@pytest.mark.parametrize('task', [ssh.EnableRootLogin, ssh.DisableRootLogin])
def test_ssh_root_login_without_openssh_server(root, task):
    task.run(DictClass(root=str(root)))
    assert not os.path.exists(root / 'etc/ssh')


@pytest.mark.parametrize('release', ['wheezy', 'bookworm'])
def test_ssh_dns_lookup_is_disabled(root, release):
    write(root / 'etc/ssh/sshd_config', SSHD_CONFIGS[release])
    ssh.DisableSSHDNSLookup.run(DictClass(root=str(root)))
    lines = read(root / 'etc/ssh/sshd_config').splitlines()
    assert lines == SSHD_CONFIGS[release].splitlines() + ['UseDNS no']


@pytest.mark.parametrize('release', list(HOST_KEY_TYPES))
def test_build_host_keys_are_shredded(root, monkeypatch, release):
    ssh_dir = root / 'etc/ssh'
    write(ssh_dir / 'sshd_config', SSHD_CONFIGS[release])
    keys = []
    for key_type in HOST_KEY_TYPES[release]:
        key = ssh_dir / 'ssh_host_{type}_key'.format(type=key_type)
        write(key, 'private key\n')
        write(ssh_dir / (key.name + '.pub'), 'public key\n')
        keys.extend([str(key), str(key) + '.pub'])
    commands = record_commands(monkeypatch, ssh)
    ssh.ShredHostkeys.run(release_info(root, release))
    [command] = commands
    assert command[:2] == ['shred', '--remove']
    assert sorted(command[2:]) == sorted(keys)


def add_ssh_key_generation(root, monkeypatch, release, openssh_installed=True):
    """Runs the tasks that install the SSH host key generation and returns the commands they issued"""
    for path in ['etc/init.d', 'etc/systemd/system', 'usr/local/sbin']:
        (root / path).mkdir(parents=True)
    commands = []

    def log_check_call(command, **kwargs):
        commands.append(command)
        if command[2] == 'dpkg-query' and not openssh_installed:
            raise CalledProcessError(1, ' '.join(command))
        return []
    monkeypatch.setattr(ssh, 'log_check_call', log_check_call)
    monkeypatch.setattr(initd, 'log_check_call', log_check_call)
    info = release_info(root, release)
    info.initd = {'install': {}, 'disable': []}
    ssh.AddSSHKeyGeneration.run(info)
    initd.InstallInitScripts.run(info)
    return commands


@pytest.mark.parametrize('release', ['wheezy', 'jessie'])
def test_ssh_host_keys_are_generated_by_an_init_script(root, monkeypatch, release):
    commands = add_ssh_key_generation(root, monkeypatch, release)
    assert commands == [['chroot', str(root), 'dpkg-query', '-W', 'openssh-server'],
                        ['chroot', str(root), 'insserv', '--default', 'generate-ssh-hostkeys']]
    script = root / 'etc/init.d/generate-ssh-hostkeys'
    assert os.access(script, os.X_OK)
    generated = sorted(key_type for key_type in ['dsa', 'rsa', 'ecdsa', 'ed25519']
                       if '-t {type} '.format(type=key_type) in read(script))
    assert generated == sorted(HOST_KEY_TYPES[release])
    assert not os.listdir(root / 'etc/systemd/system')


def test_ssh_host_keys_are_generated_by_a_systemd_unit(root, monkeypatch):
    commands = add_ssh_key_generation(root, monkeypatch, 'bookworm')
    assert commands == [['chroot', str(root), 'dpkg-query', '-W', 'openssh-server'],
                        ['chroot', str(root), 'systemctl', 'enable', 'ssh-generate-hostkeys.service']]
    unit = read(root / 'etc/systemd/system/ssh-generate-hostkeys.service')
    [exec_start] = [line.split('=', 1)[1] for line in unit.splitlines() if line.startswith('ExecStart=')]
    script = root / exec_start.lstrip('/')
    assert read(script) == read(Path(assets, 'ssh-generate-hostkeys'))
    assert stat.S_IMODE(script.stat().st_mode) == 0o750
    assert not os.listdir(root / 'etc/init.d')


@pytest.mark.parametrize('release', ['wheezy', 'bookworm'])
def test_ssh_host_key_generation_without_openssh_server(root, monkeypatch, release):
    commands = add_ssh_key_generation(root, monkeypatch, release, openssh_installed=False)
    assert commands == [['chroot', str(root), 'dpkg-query', '-W', 'openssh-server']]
    for path in ['etc/init.d', 'etc/systemd/system', 'usr/local/sbin']:
        assert not os.listdir(root / path)


def test_shadow_passwords_are_enabled(root, monkeypatch):
    commands = record_commands(monkeypatch, tools)
    security.EnableShadowConfig.run(DictClass(root=str(root)))
    assert commands == [['chroot', str(root), 'shadowconfig', 'on']]


def test_temporary_files_and_bootstrap_logs_are_removed(root):
    write(root / 'tmp/file', 'file\n')
    write(root / 'tmp/.hidden', 'hidden\n')
    write(root / 'tmp/directory/file', 'file\n')
    for log in ['bootstrap.log', 'dpkg.log', 'alternatives.log', 'apt/history.log']:
        write(root / 'var/log' / log, 'log\n')
    cleanup.CleanTMP.run(DictClass(root=str(root)))
    assert not os.listdir(root / 'tmp')
    assert sorted(os.listdir(root / 'var/log')) == ['alternatives.log', 'apt']


@pytest.mark.parametrize('release', list(MACHINE_IDS))
def test_machine_id_is_cleared(root, release):
    machine_ids = MACHINE_IDS[release]
    (root / 'tmp').mkdir()
    for log in ['bootstrap.log', 'dpkg.log']:
        write(root / 'var/log' / log, 'log\n')
    for path in machine_ids:
        write(root / path, '0123456789abcdef0123456789abcdef\n')
    info = release_info(root, release)
    for task in task_groups.get_cleanup_group(info.manifest):
        task.run(info)
    # The files stay, but empty, so that every instance of the image gets its own id on the first boot
    for path in ['etc/machine-id', 'var/lib/dbus/machine-id']:
        if path in machine_ids:
            assert read(root / path) == ''
        else:
            assert not os.path.exists(root / path)


def test_dpkg_configuration_directory_is_created_before_bootstrapping(root):
    dpkg.CreateDpkgCfg.run(DictClass(root=str(root)))
    assert os.listdir(root / 'etc/dpkg/dpkg.cfg.d') == []


def test_dpkg_configuration_directory_of_a_prebootstrapped_image_is_kept(root):
    write(root / 'etc/dpkg/dpkg.cfg.d/01_nodoc', 'path-exclude /usr/share/doc/*\n')
    dpkg.CreateDpkgCfg.run(DictClass(root=str(root)))
    assert read(root / 'etc/dpkg/dpkg.cfg.d/01_nodoc') == 'path-exclude /usr/share/doc/*\n'


def test_folder_volume(tmp_path):
    data = load_data(docker_example)
    data['bootstrapper']['workspace'] = str(tmp_path)
    info = BootstrapInformation(manifest=Manifest(path=docker_example, data=data))
    workspace.CreateWorkspace.run(info)
    folder.Create.run(info)
    assert info.root == os.path.join(info.workspace, 'root')
    assert os.listdir(info.workspace) == ['root']
    write(Path(info.root, 'etc/hostname'), 'image\n')
    folder.Delete.run(info)
    assert not hasattr(info, 'root')
    workspace.DeleteWorkspace.run(info)
    assert not os.listdir(tmp_path)
