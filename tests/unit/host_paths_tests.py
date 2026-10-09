import os.path
import shutil
from glob import glob

import pytest

from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import create_list, get_all_tasks, load_tasks
from bootstrapvz.common import releases, task_groups, tools
from bootstrapvz.common.tasks import cleanup, kernel, locale
from bootstrapvz.common.tools import load_data
from bootstrapvz.providers.kvm.tasks import virtio

kvm_examples = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests/examples/kvm')
virtio_manifests = sorted(path for path in glob(os.path.join(kvm_examples, '*.y*ml'))
                          if 'virtio' in load_data(path)['provider'])
real_copy = shutil.copy


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    def refuse(command, *args, **kwargs):
        raise AssertionError('runs a command: ' + ' '.join(command))
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(tools, 'log_call', refuse)


@pytest.fixture(name='root')
def fixture_root(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    return root


def assert_inside(root, path):
    """Fails before a task touches a path that resolves outside of the image root"""
    assert os.path.realpath(path).startswith(os.path.join(os.path.realpath(root), '')), \
        'touches the build host: ' + path


def confine_open(monkeypatch, root, module):
    def confined_open(path, *args, **kwargs):
        assert_inside(root, path)
        return open(path, *args, **kwargs)
    monkeypatch.setattr(module, 'open', confined_open, raising=False)


def test_virtio_lists_modules_in_image(root, monkeypatch):
    modules = root / 'etc/initramfs-tools/modules'
    modules.parent.mkdir(parents=True)
    modules.write_text('# existing\n', encoding='utf-8')
    confine_open(monkeypatch, root, virtio)
    info = DictClass(root=str(root),
                     manifest=DictClass(provider={'virtio': ['virtio_blk', 'virtio_net']}))
    virtio.VirtIO.run(info)
    assert modules.read_text(encoding='utf-8') == '# existing\n\nvirtio_blk\nvirtio_net\n'


@pytest.mark.parametrize('manifest_path', virtio_manifests,
                         ids=[os.path.basename(path) for path in virtio_manifests])
def test_virtio_runs_before_initramfs_rebuild(manifest_path):
    manifest = Manifest(path=manifest_path)
    tasks = load_tasks('resolve_tasks', manifest)
    all_tasks = set(get_all_tasks([manifest.modules['provider']] + manifest.modules['plugins']))
    tasklist = create_list(tasks, all_tasks)
    assert tasklist.index(virtio.VirtIO) < tasklist.index(kernel.UpdateInitramfs)
    # The order above must not be a coincidence of the topological sort
    assert kernel.UpdateInitramfs in virtio.VirtIO.successors


def test_set_local_time_copy_uses_image_zoneinfo(root, monkeypatch):
    zoneinfo = root / 'usr/share/zoneinfo/Europe/Berlin'
    zoneinfo.parent.mkdir(parents=True)
    zoneinfo.write_bytes(b'image zoneinfo')
    (root / 'etc').mkdir()
    (root / 'etc/localtime').write_bytes(b'bootstrapped zoneinfo')

    def confined_copy(src, dst):
        assert_inside(root, src)
        assert_inside(root, dst)
        return real_copy(src, dst)
    monkeypatch.setattr(shutil, 'copy', confined_copy)
    info = DictClass(root=str(root), manifest=DictClass(system={'timezone': 'Europe/Berlin'}))
    locale.SetLocalTimeCopy.run(info)
    assert (root / 'etc/localtime').read_bytes() == b'image zoneinfo'


@pytest.mark.parametrize('release', [releases.wheezy, releases.jessie, releases.trixie], ids=str)
def test_cleanup_leaves_host_motd_alone(tmp_path, root, monkeypatch, release):
    # wheezy links /etc/motd to /var/run/motd, which resolves against the host outside the chroot
    host_motd = tmp_path / 'host-motd'
    host_motd.write_text('host motd\n', encoding='utf-8')
    (root / 'etc').mkdir()
    (root / 'etc/motd').symlink_to(host_motd)
    (root / 'etc/machine-id').write_text('machine id\n', encoding='utf-8')
    (root / 'tmp').mkdir()
    (root / 'var/log').mkdir(parents=True)
    (root / 'var/log/bootstrap.log').touch()
    (root / 'var/log/dpkg.log').touch()
    confine_open(monkeypatch, root, cleanup)
    info = DictClass(root=str(root))
    for task in task_groups.get_cleanup_group(DictClass(release=release)):
        task.run(info)
    assert os.readlink(root / 'etc/motd') == str(host_motd)
    assert host_motd.read_text(encoding='utf-8') == 'host motd\n'
