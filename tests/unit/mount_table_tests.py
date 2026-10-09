import os.path
import pytest
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common.tasks import filesystem
from bootstrapvz.common.tools import load_data

manifests = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests')

# One manifest per provider
EXAMPLES = ['examples/azure/jessie.yml',
            'examples/docker/stretch.yml',
            'examples/ec2/ebs-testing-amd64-pvm.yml',
            'official/gce/buster.yml',
            'examples/kvm/buster-cloudimg.yml',
            'official/oracle/jessie.yml',
            'examples/virtualbox/stretch-vagrant.yml',
            ]

MOUNT_TABLE_TASKS = {filesystem.CopyMountTable, filesystem.RemoveMountTable}


def resolve_tasks(example, release):
    path = os.path.join(manifests, example)
    data = load_data(path)
    data['system']['release'] = release
    return load_tasks('resolve_tasks', Manifest(path=path, data=data))


@pytest.mark.parametrize('release', ['bookworm', 'trixie', 'sid'])
@pytest.mark.parametrize('example', EXAMPLES)
def test_no_mount_table_from_bookworm(example, release):
    assert resolve_tasks(example, release) & MOUNT_TABLE_TASKS == set()


@pytest.mark.parametrize('example', EXAMPLES)
def test_mount_table_before_bookworm(example):
    assert resolve_tasks(example, 'bullseye') & MOUNT_TABLE_TASKS == MOUNT_TABLE_TASKS
