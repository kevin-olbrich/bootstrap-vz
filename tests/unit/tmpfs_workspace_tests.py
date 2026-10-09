import os.path
import subprocess
import pytest
from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import create_list, get_all_tasks, load_tasks
from bootstrapvz.common import tools
from bootstrapvz.common.tasks import workspace
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.tmpfs_workspace import tasks
from bootstrapvz.plugins.vagrant import tasks as vagrant_tasks

# The vagrant plugin creates its box directory in the workspace as early as the preparation phase
example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/virtualbox/stretch-vagrant.yml')


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    """Fails the test instead of running a command that the test did not mock"""
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {args}'.format(args=args or kwargs))
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)


def test_workspace_is_mounted_before_and_unmounted_after_it_is_used():
    data = load_data(example)
    data['plugins']['tmpfs_workspace'] = {}
    manifest = Manifest(path=example, data=data)
    taskset = load_tasks('resolve_tasks', manifest)
    assert workspace.CreateWorkspace not in taskset
    assert workspace.DeleteWorkspace not in taskset
    all_tasks = set(get_all_tasks([manifest.modules['provider']] + manifest.modules['plugins']))
    tasklist = create_list(taskset, all_tasks)
    assert (tasklist.index(tasks.CreateTmpFsWorkspace) <
            tasklist.index(tasks.MountTmpFsWorkspace) <
            tasklist.index(vagrant_tasks.CreateVagrantBoxDir))
    assert (tasklist.index(vagrant_tasks.RemoveVagrantBoxDir) <
            tasklist.index(tasks.UnmountTmpFsWorkspace) <
            tasklist.index(tasks.DeleteTmpFsWorkspace))
    # The order above must not be a coincidence of the topological sort:
    # tasks that use the workspace are ordered against the tasks that the plugin replaces
    assert workspace.CreateWorkspace in tasks.MountTmpFsWorkspace.successors
    assert workspace.DeleteWorkspace in tasks.UnmountTmpFsWorkspace.predecessors
    assert workspace.CreateWorkspace in vagrant_tasks.CreateVagrantBoxDir.predecessors
    assert workspace.DeleteWorkspace in vagrant_tasks.RemoveVagrantBoxDir.successors
    # The directory exists before it is mounted and is removed only after it is unmounted
    # (the name tie-break of the sort alone would already put Create before Mount)
    assert tasks.CreateTmpFsWorkspace in tasks.MountTmpFsWorkspace.predecessors
    assert tasks.UnmountTmpFsWorkspace in tasks.DeleteTmpFsWorkspace.predecessors


def test_workspace_is_a_tmpfs_mount(tmp_path, monkeypatch):
    commands = []

    def log_check_call(command):
        commands.append(command)
        return []
    monkeypatch.setattr(tasks, 'log_check_call', log_check_call)
    path = tmp_path / 'target' / '0123abcd'
    info = DictClass(workspace=str(path))

    tasks.CreateTmpFsWorkspace.run(info)
    assert path.is_dir()
    tasks.MountTmpFsWorkspace.run(info)
    assert commands == [['mount', '--types', 'tmpfs', 'none', str(path)]]

    tasks.UnmountTmpFsWorkspace.run(info)
    assert commands[1:] == [['umount', str(path)]]
    tasks.DeleteTmpFsWorkspace.run(info)
    assert not path.exists()
    # Only the workspace of this run is removed
    assert (tmp_path / 'target').is_dir()
