import os.path
import re
import shlex
import subprocess

import pytest

from bootstrapvz.base.bootstrapinfo import BootstrapInformation, DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import create_list, get_all_tasks, load_tasks
from bootstrapvz.common import tools
from bootstrapvz.common.tools import load_data
from bootstrapvz.providers.docker.tasks import commands, image, settings

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/docker/stretch.yml')


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {args}'.format(args=args))
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)


def bootstrap_info(root, provider=None):
    data = load_data(example)
    data['provider'].update(provider or {})
    # A DictClass, because the namespace of the provider (info._docker) only exists at runtime
    info = DictClass(vars(BootstrapInformation(Manifest(path=example, data=data))))
    info.root = str(root)
    return info


def test_docker_is_required_on_the_host(tmp_path):
    info = bootstrap_info(tmp_path)
    commands.AddRequiredCommands.run(info)
    assert info.host_dependencies == {'docker': 'docker.io'}


def test_dpkg_does_not_sync(tmp_path):
    os.makedirs(os.path.join(tmp_path, 'etc/dpkg/dpkg.cfg.d'))
    settings.DpkgUnsafeIo.run(bootstrap_info(tmp_path))
    with open(os.path.join(tmp_path, 'etc/dpkg/dpkg.cfg.d/docker-apt-speedup'), encoding='utf-8') as config:
        options = [line.strip() for line in config if line.strip() and not line.startswith('#')]
    assert options == ['force-unsafe-io']


@pytest.mark.parametrize('present', [True, False])
def test_apt_may_autoremove_kernels(tmp_path, present):
    autoremove_kernels = os.path.join(tmp_path, 'etc/apt/apt.conf.d/01autoremove-kernels')
    os.makedirs(os.path.dirname(autoremove_kernels))
    if present:
        with open(autoremove_kernels, 'w', encoding='utf-8') as config:
            config.write('APT::NeverAutoRemove { "^linux-image-6\\.1\\.0-18-amd64$"; };\n')
    settings.AutoRemoveKernel.run(bootstrap_info(tmp_path))
    assert not os.path.exists(autoremove_kernels)


def test_systemd_detects_docker(tmp_path):
    settings.SystemdContainer.run(bootstrap_info(tmp_path))
    with open(os.path.join(tmp_path, 'run/systemd/container'), encoding='utf-8') as container:
        assert container.read() == 'docker'


def test_image_is_imported_with_labels_and_dockerfile_instructions(tmp_path, monkeypatch):
    labels = {'name': 'debian-{system.release}', 'summary': 'Debian {system.release} {system.architecture}'}
    info = bootstrap_info(tmp_path, {'labels': labels, 'dockerfile': ['CMD /bin/bash']})
    info.volume.create(os.path.join(tmp_path, 'root'))
    calls = []

    def log_check_call(command, **kwargs):
        calls.append((command, kwargs))
        return ['sha256:0123456789abcdef']
    monkeypatch.setattr(image, 'log_check_call', log_check_call)
    manifest = info.manifest
    all_tasks = set(get_all_tasks([manifest.modules['provider']] + manifest.modules['plugins']))
    for task in create_list(load_tasks('resolve_tasks', manifest), all_tasks):
        if task.__module__ == image.__name__:
            task.run(info)

    [([command], kwargs)] = calls
    assert kwargs == {'shell': True}
    words = shlex.split(command)
    pipe = words.index('|')
    assert words[:pipe] == ['tar', '--create', '--numeric-owner', '--directory', info.volume.path, '.']
    docker_import = words[pipe + 1:]
    assert docker_import[:2] == ['docker', 'import']
    assert docker_import[-2:] == ['-', 'debian-stretch-amd64:latest']
    changes = docker_import[2:-2]
    assert changes[::2] == ['--change'] * (len(changes) // 2)
    instructions = changes[1::2]
    build_dates = [instruction for instruction in instructions if instruction.startswith('LABEL build-date=')]
    assert len(build_dates) == 1
    assert re.fullmatch(r'LABEL build-date=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', build_dates[0])
    # The labels of the manifest replace the generated ones of the same name, and docker
    # parses the values like a Dockerfile, so values with spaces stay quoted
    assert sorted(set(instructions) - set(build_dates)) == sorted(['LABEL name=debian-stretch',
                                                                   'LABEL architecture=amd64',
                                                                   "LABEL summary='Debian stretch amd64'",
                                                                   'CMD /bin/bash'])
    # The instructions of the manifest come last, so they win over the generated labels
    assert instructions[-1] == 'CMD /bin/bash'
    assert info._docker['image_id'] == 'sha256:0123456789abcdef'
