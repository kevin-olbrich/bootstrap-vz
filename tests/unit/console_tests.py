import os.path
import pytest
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common.tasks import grub
from bootstrapvz.common.tools import load_data
from bootstrapvz.providers.kvm.tasks import boot as kvm_boot
from bootstrapvz.providers.virtualbox.tasks import boot as virtualbox_boot

examples = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests/examples')
kvm_example = os.path.join(examples, 'kvm/buster-console.yml')
virtualbox_example = os.path.join(examples, 'virtualbox/stretch-vagrant.yml')


def resolve_tasks(path, bootloader):
    data = load_data(path)
    data['system']['bootloader'] = bootloader
    return load_tasks('resolve_tasks', Manifest(path=path, data=data))


def grub_config_tasks(tasks):
    # These tasks edit info.grub_config, which only grub.InitGrubConfig creates
    return {task for task in tasks if grub.WriteGrubConfig in task.successors}


def test_virtualbox_extlinux_has_no_grub_config_tasks():
    tasks = resolve_tasks(virtualbox_example, 'extlinux')
    assert grub.InitGrubConfig not in tasks
    assert not grub_config_tasks(tasks)


def test_virtualbox_grub_adds_virtual_console():
    assert virtualbox_boot.AddVirtualConsoleGrubOutputDevice in resolve_tasks(virtualbox_example, 'grub')


@pytest.mark.parametrize('bootloader', ['extlinux', 'none'])
def test_kvm_virtual_console_without_grub_has_no_grub_config_tasks(bootloader):
    tasks = resolve_tasks(kvm_example, bootloader)
    assert grub.InitGrubConfig not in tasks
    assert not grub_config_tasks(tasks)
    # Keeping the boot messages on tty1 does not depend on the bootloader
    assert kvm_boot.SetSystemdTTYVTDisallocate in tasks


def test_kvm_virtual_console_with_grub():
    tasks = resolve_tasks(kvm_example, 'grub')
    assert kvm_boot.SetGrubConsolOutputDeviceToVirtual in tasks
    assert kvm_boot.SetSystemdTTYVTDisallocate in tasks
