import os.path
import subprocess

import pytest

from bootstrapvz.base.bootstrapinfo import BootstrapInformation
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common.exceptions import TaskError
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.ansible import tasks

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


def ansible_info(tmp_path, release='buster', **settings):
    data = load_data(example)
    data['bootstrapper']['workspace'] = str(tmp_path)
    data['system']['release'] = release
    data['plugins'] = {'ansible': {'playbook': 'playbooks/site.yml', **settings}}
    # The manifest lives in tmp_path, so that the playbook path resolves there
    info = BootstrapInformation(manifest=Manifest(path=str(tmp_path / 'manifest.yml'), data=data))
    info.root = str(tmp_path / 'root')
    os.makedirs(os.path.join(info.root, 'tmp'))
    return info


def run_playbook(info, monkeypatch):
    """Runs the playbook task and returns the commands it ran, each with the inventory at that time"""
    inventory = os.path.join(info.root, 'tmp/bootstrap-inventory')
    runs = []

    def log_check_call(command):
        with open(inventory, encoding='utf-8') as handle:
            runs.append((command, handle.read()))
        return []
    monkeypatch.setattr(tasks, 'log_check_call', log_check_call)
    tasks.RunAnsiblePlaybook.run(info)
    # The inventory is not left behind in the image
    assert not os.path.exists(inventory)
    return runs


def test_playbook_runs_against_image_root(tmp_path, monkeypatch):
    info = ansible_info(tmp_path)
    runs = run_playbook(info, monkeypatch)
    inventory = os.path.join(info.root, 'tmp/bootstrap-inventory')
    playbook = str(tmp_path / 'playbooks/site.yml')
    assert runs == [(['ansible-playbook', '-i', inventory, playbook],
                     info.root + ' ansible_connection=chroot')]


def test_playbook_options_passed_to_ansible(tmp_path, monkeypatch):
    info = ansible_info(tmp_path, groups=['web', 'db'], extra_vars={'ansible_ssh_user': 'deploy', 'port': 8080},
                        tags=['base', 'web'], skip_tags=['slow'], opt_flags=['--diff', '--check'])
    runs = run_playbook(info, monkeypatch)
    inventory = os.path.join(info.root, 'tmp/bootstrap-inventory')
    playbook = str(tmp_path / 'playbooks/site.yml')
    host = info.root + ' ansible_connection=chroot'
    assert runs == [(['ansible-playbook', '-i', inventory, playbook,
                      '--extra-vars', '{"ansible_ssh_user": "deploy", "port": 8080}',
                      '--tags', 'base,web', '--skip-tags', 'slow', '--diff', '--check'],
                     '[web]\n{host}\n[db]\n{host}\n'.format(host=host))]


def test_ssh_user_ansible_dir_removed(tmp_path, monkeypatch):
    info = ansible_info(tmp_path, extra_vars={'ansible_ssh_user': 'deploy'})
    home = os.path.join(info.root, 'home/deploy')
    os.makedirs(os.path.join(home, '.ansible/tmp'))
    os.makedirs(os.path.join(home, '.ssh'))
    commands = []

    def log_check_call(command):
        commands.append(command)
        return ['/home/deploy']
    monkeypatch.setattr(tasks, 'log_check_call', log_check_call)
    tasks.RemoveAnsibleSSHUserDir.run(info)
    # The home directory is looked up in the image, where the user exists
    assert commands == [['chroot', info.root, 'sh', '-c', 'echo ~deploy']]
    assert os.listdir(home) == ['.ssh']


@pytest.mark.parametrize('extra_vars, remove', [({'ansible_ssh_user': 'deploy'}, True),
                                                ({'port': 8080}, False)])
def test_ssh_user_ansible_dir_removal_resolved(tmp_path, extra_vars, remove):
    info = ansible_info(tmp_path, extra_vars=extra_vars)
    assert (tasks.RemoveAnsibleSSHUserDir in load_tasks('resolve_tasks', info.manifest)) == remove


def test_playbook_path_checked(tmp_path):
    info = ansible_info(tmp_path)
    playbook = tmp_path / 'playbooks/site.yml'
    with pytest.raises(TaskError, match='does not exist'):
        tasks.CheckPlaybookPath.run(info)
    playbook.mkdir(parents=True)
    with pytest.raises(TaskError, match='does not point to a file'):
        tasks.CheckPlaybookPath.run(info)
    playbook.rmdir()
    playbook.write_text('- hosts: all\n', encoding='utf-8')
    tasks.CheckPlaybookPath.run(info)


@pytest.mark.parametrize('release, python', [('stretch', 'python'), ('buster', 'python'),
                                             ('bullseye', 'python3'), ('trixie', 'python3')])
def test_python_installed_for_ansible(tmp_path, release, python):
    info = ansible_info(tmp_path, release=release)
    tasks.AddPackages.run(info)
    assert [str(package) for package in info.packages.install] == [python]
