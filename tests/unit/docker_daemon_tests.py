import filecmp
import os.path
import stat
import subprocess
import pytest
from bootstrapvz.base.bootstrapinfo import BootstrapInformation
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common.exceptions import TaskError
from bootstrapvz.common.tasks import grub
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.docker_daemon import tasks

manifests = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests')


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


def resolve_tasks(path, bootloader):
    path = os.path.join(manifests, path)
    data = load_data(path)
    data['system']['release'] = 'bookworm'
    data['system']['bootloader'] = bootloader
    data['plugins'] = {'docker_daemon': {}}
    return load_tasks('resolve_tasks', Manifest(path=path, data=data))


@pytest.mark.parametrize('path, bootloader', [('examples/kvm/buster-cloudimg.yml', 'grub'),
                                              ('official/ec2/ebs-stretch-amd64-hvm.yml', 'grub'),
                                              ('examples/ec2/ebs-testing-amd64-pvm.yml', 'pvgrub')])
def test_memory_cgroup_enabled_with_grub(path, bootloader):
    taskset = resolve_tasks(path, bootloader)
    assert grub.InitGrubConfig in taskset
    assert tasks.EnableMemoryCgroup in taskset


@pytest.mark.parametrize('path, bootloader', [('examples/kvm/buster-cloudimg.yml', 'extlinux'),
                                              ('examples/kvm/buster-cloudimg.yml', 'none'),
                                              ('official/ec2/ebs-stretch-amd64-hvm.yml', 'extlinux'),
                                              ('examples/docker/stretch.yml', 'none')])
def test_memory_cgroup_skipped_without_grub(path, bootloader):
    # EnableMemoryCgroup edits info.grub_config, which only exists when InitGrubConfig runs
    taskset = resolve_tasks(path, bootloader)
    assert grub.InitGrubConfig not in taskset
    assert tasks.EnableMemoryCgroup not in taskset


def docker_info(tmp_path, settings):
    path = os.path.join(manifests, 'examples/kvm/buster-cloudimg.yml')
    data = load_data(path)
    data['bootstrapper']['workspace'] = str(tmp_path)
    data['system']['release'] = 'bookworm'
    data['plugins'] = {'docker_daemon': settings}
    info = BootstrapInformation(manifest=Manifest(path=path, data=data))
    info.root = str(tmp_path / 'root')
    return info


def test_docker_repository_signed_by_shipped_key(tmp_path):
    info = docker_info(tmp_path, {})
    tasks.AddDockerAptSource.run(info)
    tasks.InstallDockerAptKey.run(info)
    [source] = info.source_lists.sources['docker']
    assert str(source) == ('deb [signed-by=/etc/apt/keyrings/docker.asc] '
                           'https://download.docker.com/linux/debian bookworm stable')
    # apt needs the CA certificates to fetch from the HTTPS repository
    assert 'ca-certificates' in info.include_packages
    keyring = os.path.join(info.root, 'etc/apt/keyrings/docker.asc')
    assert filecmp.cmp(keyring, os.path.join(tasks.ASSETS_DIR, 'docker.asc'), shallow=False)
    assert stat.S_IMODE(os.stat(keyring).st_mode) == 0o644


def test_docker_version_pinned(tmp_path):
    info = docker_info(tmp_path, {'version': '29.8.1'})
    tasks.PinDockerVersion.run(info)
    assert [str(preference) for preference in info.preference_lists.preferences['docker']] == [
        'Package: docker-ce docker-ce-cli\nPin: version 5:29.8.1-*\nPin-Priority: 1001\n']


def test_docker_opts_added_to_daemon_command(tmp_path):
    info = docker_info(tmp_path, {'docker_opts': '--dns 8.8.8.8'})
    tasks.SetDockerOpts.run(info)
    with open(os.path.join(info.root, 'etc/systemd/system/docker.service.d/bootstrap-vz.conf'),
              encoding='utf-8') as dropin:
        # The empty ExecStart clears the command of the packaged unit
        assert dropin.read() == ('[Service]\n'
                                 'ExecStart=\n'
                                 'ExecStart=/usr/bin/dockerd -H fd:// --containerd=/run/containerd/containerd.sock '
                                 '--dns 8.8.8.8\n')


class Daemon:
    """A daemon process that is never started"""
    def __init__(self, args):
        self.args = args
        self.running = True

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def terminate(self):
        self.running = False

    def wait(self, timeout=None):
        return 0

    def option(self, name):
        return self.args[self.args.index(name) + 1]


def pull_images(info, monkeypatch, fail, commands):
    """Runs PullDockerImages, adds the docker commands it runs to commands and returns the daemons
    it started. fail(command, attempt) tells whether the command fails the given time it runs.
    """
    daemons = []

    def popen(args, **kwargs):
        daemons.append(Daemon(args))
        return daemons[-1]

    def log_check_call(command):
        commands.append(command)
        if fail(command, commands.count(command)):
            raise subprocess.CalledProcessError(1, command)
        return []
    monkeypatch.setattr(subprocess, 'Popen', popen)
    monkeypatch.setattr(tasks, 'log_check_call', log_check_call)
    monkeypatch.setattr(tasks.time, 'sleep', lambda seconds: None)
    try:
        tasks.PullDockerImages.run(info)
    finally:
        # The daemon must not outlive the task
        assert daemons
        assert not [daemon for daemon in daemons if daemon.running]
    return daemons


def test_images_pulled_with_daemon_from_image(tmp_path, monkeypatch):
    info = docker_info(tmp_path, {'pull_images': ['debian:bookworm', '/srv/images/app.tar.gz']})

    def fail(command, attempt):
        # The daemon answers from the second attempt on
        return command[-1] == 'version' and attempt == 1
    commands = []
    daemons = pull_images(info, monkeypatch, fail, commands)
    bin_dir = os.path.join(info.root, 'usr/bin')
    [dockerd] = [daemon for daemon in daemons if daemon.args[0] == os.path.join(bin_dir, 'dockerd')]
    # Pulled images end up in the image, not in the Docker data of the build host
    assert dockerd.option('--data-root') == os.path.join(info.root, 'var/lib/docker')
    socket = dockerd.option('--host')
    assert socket.startswith('unix://' + info.workspace + '/')
    docker = [os.path.join(bin_dir, 'docker'), '-H', socket]
    assert commands == [docker + ['version'],
                        docker + ['version'],
                        docker + ['pull', 'debian:bookworm'],
                        docker + ['load', '--input', '/srv/images/app.tar.gz']]


def test_daemon_that_does_not_answer_fails_task(tmp_path, monkeypatch):
    info = docker_info(tmp_path, {'pull_images': ['debian:bookworm'], 'pull_images_retries': 3})
    commands = []
    with pytest.raises(TaskError):
        pull_images(info, monkeypatch, lambda command, attempt: True, commands)
    # One attempt per second of pull_images_retries, and nothing is pulled
    assert [command[-1] for command in commands] == ['version', 'version', 'version']


def test_failed_pull_fails_task(tmp_path, monkeypatch):
    info = docker_info(tmp_path, {'pull_images': ['debian:bookworm', 'debian:trixie']})
    commands = []
    with pytest.raises(TaskError, match='debian:bookworm'):
        pull_images(info, monkeypatch, lambda command, attempt: command[-1] == 'debian:bookworm', commands)
    assert [command[-1] for command in commands] == ['version', 'debian:bookworm']
