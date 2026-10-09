import os.path
from types import SimpleNamespace
import pytest
from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import create_list, get_all_tasks, load_tasks
from bootstrapvz.common.tasks import extlinux, packages
from bootstrapvz.common.tools import load_data

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/buster-cloudimg.yml')

BUILD_KERNEL_VERSION = '6.1.0-9-amd64'
ROOT_UUID = '0b6b8a5e-2f6e-4c43-9d1a-3f1e5c7b9a21'


@pytest.mark.parametrize('separate_boot, boot_prefix', [(False, '/boot'), (True, '')])
def test_extlinux_boots_the_default_kernel_symlinks(tmp_path, separate_boot, boot_prefix):
    partition_map = SimpleNamespace(root=SimpleNamespace(get_uuid=lambda: ROOT_UUID))
    if separate_boot:
        partition_map.boot = SimpleNamespace()
    info = DictClass(root=str(tmp_path), kernel_version=BUILD_KERNEL_VERSION,
                     volume=SimpleNamespace(partition_map=partition_map))
    (tmp_path / 'boot').mkdir()
    extlinux.ConfigureExtlinuxJessie.run(info)
    config = (tmp_path / 'boot/extlinux/extlinux.conf').read_text(encoding='utf-8')
    # Kernel updates must not leave the image booting the kernel it was built with
    assert BUILD_KERNEL_VERSION not in config
    # Both entries boot the symlinks that the kernel packages point at the default kernel
    assert config.count('\tlinux {prefix}/vmlinuz\n'.format(prefix=boot_prefix)) == 2
    assert config.count('\tappend initrd={prefix}/initrd.img root=UUID={uuid} '
                        .format(prefix=boot_prefix, uuid=ROOT_UUID)) == 2


def test_kernel_symlinks_are_kept_in_boot(tmp_path):
    (tmp_path / 'etc').mkdir()
    extlinux.LinkKernelInBoot.run(DictClass(root=str(tmp_path)))
    assert (tmp_path / 'etc/kernel-img.conf').read_text(encoding='utf-8') == 'link_in_boot = yes\n'


@pytest.mark.parametrize('release', ['jessie', 'bookworm', 'trixie'])
def test_kernel_symlinks_are_configured_before_the_kernel_is_installed(release):
    data = load_data(example)
    data['system']['release'] = release
    data['system']['bootloader'] = 'extlinux'
    manifest = Manifest(path=example, data=data)
    all_tasks = set(get_all_tasks([manifest.modules['provider']] + manifest.modules['plugins']))
    task_list = create_list(load_tasks('resolve_tasks', manifest), all_tasks)
    assert task_list.index(extlinux.LinkKernelInBoot) < task_list.index(packages.InstallPackages)
