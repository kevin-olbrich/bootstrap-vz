import os.path
import subprocess

import pytest

from bootstrapvz.base.bootstrapinfo import BootstrapInformation, DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import create_list, get_all_tasks, load_tasks
from bootstrapvz.common import tools
from bootstrapvz.common.tasks import grub
from bootstrapvz.common.tools import load_data
from bootstrapvz.providers.gce.tasks import apt, configuration, image, packages

manifests = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests/official/gce')
example = os.path.join(manifests, 'buster.yml')

LSB_RELEASE = {'-i': ['Debian'], '-d': ['Debian GNU/Linux 10 (buster)'], '-r': ['10']}


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {args}'.format(args=args))
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)


@pytest.fixture(name='commands')
def fixture_commands(monkeypatch):
    """Records the commands of the GCE tasks and answers lsb_release like a buster image"""
    commands = []

    def log_check_call(command):
        commands.append(command)
        if command[2] == 'lsb_release':
            return LSB_RELEASE[command[3]]
        return []
    for module in [apt, configuration, image]:
        monkeypatch.setattr(module, 'log_check_call', log_check_call)
    return commands


def manifest_data(root, path=example):
    data = load_data(path)
    data['bootstrapper']['workspace'] = str(root)
    return data


def bootstrap_info(root, data):
    # A DictClass, because the namespace of the provider (info._gce) only exists at runtime
    info = DictClass(vars(BootstrapInformation(Manifest(path=example, data=data))))
    info.root = os.path.join(root, 'root')
    return info


def kernel_parameters(info):
    """Runs the tasks that fill the grub configuration in tasklist order
    and returns the kernel parameters from /etc/default/grub"""
    manifest = info.manifest
    all_tasks = set(get_all_tasks([manifest.modules['provider']] + manifest.modules['plugins']))
    os.makedirs(os.path.join(info.root, 'etc/default'))
    for task in create_list(load_tasks('resolve_tasks', manifest), all_tasks):
        if task in (grub.InitGrubConfig, grub.WriteGrubConfig) or grub.WriteGrubConfig in task.successors:
            task.run(info)
    with open(os.path.join(info.root, 'etc/default/grub'), encoding='utf-8') as defaults:
        for line in defaults:
            if line.startswith('GRUB_CMDLINE_LINUX='):
                return line.rstrip('\n').split('=', 1)[1].strip('"').split()
    return []


@pytest.mark.parametrize('release, multiqueue', [('jessie', False), ('stretch', True), ('buster', True)])
def test_kernel_logs_to_the_serial_port(tmp_path, release, multiqueue):
    data = manifest_data(tmp_path, os.path.join(manifests, release + '.yml'))
    parameters = kernel_parameters(bootstrap_info(tmp_path, data))
    consoles = [parameter for parameter in parameters if parameter.startswith('console=')]
    # GCE shows the serial port output at 38400 baud, the generic serial console is left out
    assert consoles == ['console=ttyS0,38400n8']
    assert ('scsi_mod.use_blk_mq=Y' in parameters) == multiqueue


@pytest.mark.parametrize('release, python', [('buster', 'python'), ('bullseye', 'python3'), ('bookworm', 'python3')])
def test_default_packages(tmp_path, release, python):
    data = manifest_data(tmp_path)
    data['system']['release'] = release
    info = bootstrap_info(tmp_path, data)
    packages.DefaultPackages.run(info)
    names = [package.name for package in info.packages.remote()]
    assert python in names
    assert {'linux-image-amd64', 'openssh-server', 'isc-dhcp-client', 'sudo', 'curl'} <= set(names)
    if python == 'python3':
        assert 'python' not in names


def test_image_ships_with_apt_lists(tmp_path, commands):
    info = bootstrap_info(tmp_path, manifest_data(tmp_path))
    apt.AddBaselineAptCache.run(info)
    assert commands == [['chroot', info.root, 'apt-get', 'update']]


def test_release_information_is_read_from_the_image(tmp_path, commands):
    info = bootstrap_info(tmp_path, manifest_data(tmp_path))
    configuration.GatherReleaseInformation.run(info)
    assert sorted(commands) == sorted([['chroot', info.root, 'lsb_release', flag, '-s']
                                       for flag in LSB_RELEASE])
    assert info._gce == {'lsb_distribution': 'Debian',
                         'lsb_description': 'Debian GNU/Linux 10 (buster)',
                         'lsb_release': '10'}


def test_image_is_packed_uploaded_and_registered(tmp_path, commands):
    data = manifest_data(tmp_path)
    data['name'] = 'Debian-{system.release}.v1'
    data['provider'].update({'gcs_destination': 'gs://images/debian/', 'gce_project': 'build-project'})
    info = bootstrap_info(tmp_path, data)
    configuration.GatherReleaseInformation.run(info)
    del commands[:]
    image.CreateTarball.run(info)
    image.UploadImage.run(info)
    image.RegisterImage.run(info)
    tarball = os.path.join(tmp_path, 'debian-buster-v1.tar.gz')
    tar, gsutil, gcloud = commands
    # GCE only imports a tarball that holds the raw disk as disk.raw
    assert tar == ['tar', '--sparse', '-C', str(tmp_path), '-caf', tarball,
                   '--transform=s|.*|disk.raw|', 'Debian-buster.v1.raw']
    assert gsutil == ['gsutil', 'cp', tarball, 'gs://images/debian/debian-buster-v1.tar.gz']
    # Image names only allow lowercase letters, digits and dashes
    assert gcloud[:6] == ['gcloud', 'compute', '--project=build-project', 'images', 'create', 'debian-buster-v1']
    assert '--source-uri=gs://images/debian/debian-buster-v1.tar.gz' in gcloud
    # The manifest says: Debian {system.release} {system.architecture}
    assert '--description=Debian buster amd64' in gcloud


def test_registered_image_without_description_is_described_by_its_release(tmp_path, commands):
    data = manifest_data(tmp_path)
    del data['provider']['description']
    data['provider'].update({'gcs_destination': 'gs://images/', 'gce_project': 'build-project'})
    info = bootstrap_info(tmp_path, data)
    configuration.GatherReleaseInformation.run(info)
    image.CreateTarball.run(info)
    image.RegisterImage.run(info)
    assert '--description=Debian GNU/Linux 10 (buster)' in commands[-1]
