import os.path
import subprocess
import pytest
from bootstrapvz.base.bootstrapinfo import BootstrapInformation
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import create_list, get_all_tasks, load_tasks
from bootstrapvz.common import tools
from bootstrapvz.common.tasks import apt
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.opennebula import tasks

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/wheezy.yml')


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    """Fails the test instead of running a command that the test did not mock"""
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {args}'.format(args=args or kwargs))
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)


def test_wheezy_installs_the_context_package_from_backports():
    data = load_data(example)
    data['plugins']['opennebula'] = {}
    manifest = Manifest(path=example, data=data)
    taskset = load_tasks('resolve_tasks', manifest)
    all_tasks = set(get_all_tasks([manifest.modules['provider']] + manifest.modules['plugins']))
    tasklist = create_list(taskset, all_tasks)
    assert tasklist.index(apt.AddBackports) < tasklist.index(tasks.AddONEContextPackage)
    # The order above must not be a coincidence of the topological sort
    assert apt.AddBackports in tasks.AddONEContextPackage.predecessors

    info = BootstrapInformation(manifest)
    apt.AddBackports.run(info)
    tasks.AddONEContextPackage.run(info)
    assert [str(package) for package in info.packages.install] == ['opennebula-context/wheezy-backports']
