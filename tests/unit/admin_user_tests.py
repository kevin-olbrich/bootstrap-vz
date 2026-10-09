import os.path
import shutil
import stat
import subprocess

import pytest

from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common import tools
from bootstrapvz.common.exceptions import ManifestError
from bootstrapvz.common.tasks import ssh
from bootstrapvz.common.tools import load_data, rel_path
from bootstrapvz.plugins.admin_user import tasks

manifests = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests')
example = os.path.join(manifests, 'examples/kvm/buster-cloudimg.yml')
# A provider that turns off SSH password login
gce_manifest = os.path.join(manifests, 'official/gce/buster.yml')
get_credentials = rel_path(tasks.__file__, '../../providers/ec2/assets/init.d/ec2-get-credentials')

PUBKEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl admin@example.org'


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
    """Records the commands that the tasks run through log_check_call, together with their stdin.
    Call it after the manifest is loaded, see no_external_commands.
    """
    commands = []

    def log_check_call(command, stdin=None, **kwargs):
        commands.append((command, stdin))
        return []
    monkeypatch.setattr(tools, 'log_check_call', log_check_call)
    return commands


def admin_user_info(tmp_path, **settings):
    data = load_data(example)
    data['plugins'] = {'admin_user': {'username': 'admin', **settings}}
    # The manifest lives in tmp_path, so that relative paths in it resolve there
    manifest = Manifest(path=str(tmp_path / 'manifest.yml'), data=data)
    root = tmp_path / 'root'
    root.mkdir()
    return DictClass(manifest=manifest, root=str(root), initd={'install': {}, 'disable': []})


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_admin_user_created_with_home_and_bash(tmp_path, monkeypatch):
    info = admin_user_info(tmp_path)
    commands = record_commands(monkeypatch)
    tasks.CreateAdminUser.run(info)
    assert commands == [(['chroot', info.root, 'useradd', '--create-home', '--shell', '/bin/bash', 'admin'], None)]


def test_admin_user_gets_passwordless_sudo(tmp_path):
    info = admin_user_info(tmp_path)
    os.makedirs(os.path.join(info.root, 'etc/sudoers.d'))
    tasks.PasswordlessSudo.run(info)
    sudoers = os.path.join(info.root, 'etc/sudoers.d/99_admin')
    with open(sudoers, encoding='utf-8') as handle:
        assert handle.read().splitlines() == ['admin ALL=(ALL) NOPASSWD:ALL']
    assert mode(sudoers) == 0o440


def test_admin_user_password_set_with_chpasswd(tmp_path, monkeypatch):
    info = admin_user_info(tmp_path, password='s3cr3t')
    commands = record_commands(monkeypatch)
    tasks.AdminUserPassword.run(info)
    assert commands == [(['chroot', info.root, 'chpasswd'], 'admin:s3cr3t')]


def test_pubkey_authorized_for_admin_user(tmp_path, monkeypatch):
    # The path of the key is relative to the manifest
    (tmp_path / 'keys').mkdir()
    (tmp_path / 'keys/admin.pub').write_text(PUBKEY + '\n', encoding='utf-8')
    info = admin_user_info(tmp_path, pubkey='keys/admin.pub')
    info.initd['install'] = {'ec2-get-credentials': get_credentials, 'expand-root': '/assets/expand-root'}
    home = os.path.join(info.root, 'home/admin')
    os.makedirs(home)
    commands = record_commands(monkeypatch)
    tasks.AdminUserPublicKey.run(info)

    authorized_keys = os.path.join(home, '.ssh/authorized_keys')
    with open(authorized_keys, encoding='utf-8') as handle:
        assert handle.read().split() == PUBKEY.split()
    # sshd ignores keys that other users can modify
    assert mode(os.path.join(home, '.ssh')) == 0o700
    assert mode(authorized_keys) == 0o600
    # Only the image knows the admin user, so the owner is set inside the chroot
    assert commands == [(['chroot', info.root, 'chown', '-R', 'admin:admin', 'home/admin/.ssh'], None)]
    # The EC2 key would be added next to the static key, so the script that fetches it is not installed
    assert info.initd['install'] == {'expand-root': '/assets/expand-root'}


def test_pubkey_added_to_existing_authorized_keys(tmp_path, monkeypatch):
    (tmp_path / 'admin.pub').write_text(PUBKEY, encoding='utf-8')
    info = admin_user_info(tmp_path, pubkey='admin.pub')
    ssh_dir = os.path.join(info.root, 'home/admin/.ssh')
    os.makedirs(ssh_dir, 0o700)
    with open(os.path.join(ssh_dir, 'authorized_keys'), 'w', encoding='utf-8') as handle:
        handle.write('ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC7 other@example.org\n')
    record_commands(monkeypatch)
    tasks.AdminUserPublicKey.run(info)
    with open(os.path.join(ssh_dir, 'authorized_keys'), encoding='utf-8') as handle:
        assert handle.read().splitlines() == ['ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC7 other@example.org', PUBKEY]


def test_ec2_key_fetched_for_admin_user(tmp_path):
    info = admin_user_info(tmp_path)
    script = os.path.join(info.root, 'etc/init.d/ec2-get-credentials')
    os.makedirs(os.path.dirname(script))
    shutil.copy(get_credentials, script)
    tasks.AdminUserPublicKeyEC2.run(info)
    with open(get_credentials, encoding='utf-8') as handle:
        original = handle.read()
    with open(script, encoding='utf-8') as handle:
        assert handle.read() == original.replace("\nusername='root'\n", "\nusername='admin'\n")


def test_missing_pubkey_rejected(tmp_path):
    info = admin_user_info(tmp_path, pubkey='missing.pub')
    with pytest.raises(ManifestError) as excinfo:
        tasks.CheckPublicKeyFile.run(info)
    assert list(excinfo.value.data_path) == ['plugins', 'admin_user', 'pubkey']


@pytest.mark.parametrize('status', [0, 255])
def test_pubkey_checked_with_ssh_keygen(tmp_path, monkeypatch, status):
    (tmp_path / 'admin.pub').write_text(PUBKEY, encoding='utf-8')
    info = admin_user_info(tmp_path, pubkey='admin.pub')
    commands = []

    def log_call(command):
        commands.append(command)
        return status, [], []
    monkeypatch.setattr(tools, 'log_call', log_call)
    if status == 0:
        tasks.CheckPublicKeyFile.run(info)
    else:
        with pytest.raises(ManifestError) as excinfo:
            tasks.CheckPublicKeyFile.run(info)
        assert list(excinfo.value.data_path) == ['plugins', 'admin_user', 'pubkey']
    assert commands == [['ssh-keygen', '-l', '-f', str(tmp_path / 'admin.pub')]]


@pytest.mark.parametrize('settings, password_login', [({'password': 's3cr3t'}, True),
                                                      ({'pubkey': '/home/admin/.ssh/id_ed25519.pub'}, False)],
                         ids=['password', 'pubkey'])
def test_password_enables_ssh_password_login(settings, password_login):
    data = load_data(gce_manifest)
    data['plugins']['admin_user'] = {'username': 'admin', **settings}
    taskset = load_tasks('resolve_tasks', Manifest(path=gce_manifest, data=data))
    assert (tasks.AdminUserPassword in taskset) == password_login
    assert (ssh.DisableSSHPasswordAuthentication not in taskset) == password_login
