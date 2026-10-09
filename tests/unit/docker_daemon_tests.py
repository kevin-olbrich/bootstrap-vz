import os.path
import pytest
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common.tasks import grub
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.docker_daemon import tasks

manifests = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests')


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
