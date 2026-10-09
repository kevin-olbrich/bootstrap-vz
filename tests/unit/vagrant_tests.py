import json
import os.path
import re
import stat
import subprocess
import xml.etree.ElementTree as ET
import pytest
from bootstrapvz.base.bootstrapinfo import BootstrapInformation, DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common import tools
from bootstrapvz.common.exceptions import TaskError
from bootstrapvz.common.tasks import image, ssh, volume
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.vagrant import tasks

examples = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests/examples')
virtualbox_example = os.path.join(examples, 'virtualbox/stretch-vagrant.yml')
# A qcow2 volume with 2GiB
kvm_example = os.path.join(examples, 'kvm/buster-cloudimg.yml')
insecure_key = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                            '../../bootstrapvz/plugins/vagrant/assets/authorized_keys')

OVF_NAMESPACES = {'ovf': 'http://schemas.dmtf.org/ovf/envelope/1',
                  'vbox': 'http://www.virtualbox.org/ovf/machine'}


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    """Fails the test instead of running a command that the test did not mock"""
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {args}'.format(args=args or kwargs))
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)


def vagrant_manifest(tmp_path, path=virtualbox_example, plugins=None):
    data = load_data(path)
    data['name'] = 'debian-{system.release}-{system.architecture}'
    data['bootstrapper']['workspace'] = str(tmp_path)
    # The vagrant schema requires a hostname, which kvm_example does not set
    data['system']['hostname'] = 'localhost'
    data['plugins'] = {'vagrant': {}} if plugins is None else plugins
    return Manifest(path=path, data=data)


def vagrant_info(tmp_path, path=virtualbox_example, settings=None):
    # Tasks only use the attributes, a DictClass also lets pylint see the namespace of the plugin
    info = DictClass(vars(BootstrapInformation(vagrant_manifest(tmp_path, path, {'vagrant': settings or {}}))))
    info.root = str(tmp_path / 'root')
    return info


def record_commands(monkeypatch, root=None):
    """Records the commands and emulates the effect of those that the following tasks rely on"""
    calls = []

    def log_check_call(command, stdin=None, **kwargs):
        calls.append((command, stdin))
        if command[:2] == ['ln', '-s']:
            os.symlink(command[2], command[3])
        if command[:3] == ['chroot', root, 'useradd']:
            os.makedirs(os.path.join(root, 'home', command[-1]))
        return []
    monkeypatch.setattr(tools, 'log_check_call', log_check_call)
    return calls


def test_box_replaces_the_image(tmp_path):
    assert image.MoveImage in load_tasks('resolve_tasks', vagrant_manifest(tmp_path, plugins={}))
    resolved = load_tasks('resolve_tasks', vagrant_manifest(tmp_path))
    assert image.MoveImage not in resolved
    assert {tasks.PackageBox, volume.Delete} <= resolved
    # Vagrant logs in over SSH, and password logins stay enabled
    assert ssh.AddOpenSSHPackage in resolved
    assert ssh.DisableSSHPasswordAuthentication not in resolved


def test_box_is_placed_in_the_bootstrapper_workspace(tmp_path):
    info = vagrant_info(tmp_path)
    tasks.CheckBoxPath.run(info)
    assert info._vagrant['box_path'] == str(tmp_path / 'debian-stretch-amd64.box')


def test_existing_box_is_not_overwritten(tmp_path):
    (tmp_path / 'debian-stretch-amd64.box').write_bytes(b'box')
    with pytest.raises(TaskError, match='debian-stretch-amd64.box'):
        tasks.CheckBoxPath.run(vagrant_info(tmp_path))
    assert (tmp_path / 'debian-stretch-amd64.box').read_bytes() == b'box'


def test_vagrant_can_log_in_and_use_sudo(tmp_path, monkeypatch):
    info = vagrant_info(tmp_path)
    root = tmp_path / 'root'
    (root / 'etc/sudoers.d').mkdir(parents=True)
    calls = record_commands(monkeypatch, info.root)

    tasks.AddPackages.run(info)
    tasks.CreateVagrantUser.run(info)
    tasks.AddInsecurePublicKey.run(info)
    tasks.PasswordlessSudo.run(info)
    tasks.SetRootPassword.run(info)

    assert sorted(str(package) for package in info.packages.install) == ['nfs-client', 'openssh-server', 'sudo']
    assert calls == [(['chroot', info.root, 'useradd', '--create-home', '--shell', '/bin/bash', 'vagrant'], None),
                     (['chroot', info.root, 'chown', 'vagrant:vagrant',
                       '/home/vagrant/.ssh', '/home/vagrant/.ssh/authorized_keys'], None),
                     (['chroot', info.root, 'chpasswd'], 'root:vagrant')]
    ssh_dir = root / 'home/vagrant/.ssh'
    # Accessible by the user only, as ssh(1) recommends
    assert stat.S_IMODE(ssh_dir.stat().st_mode) == 0o700
    authorized_keys = ssh_dir / 'authorized_keys'
    assert stat.S_IMODE(authorized_keys.stat().st_mode) == 0o600
    with open(insecure_key, encoding='utf-8') as key:
        assert authorized_keys.read_text(encoding='utf-8') == key.read()
    sudoers = root / 'etc/sudoers.d/vagrant'
    assert sudoers.read_text(encoding='utf-8').strip() == 'vagrant ALL=(ALL) NOPASSWD:ALL'
    # The mode that sudo expects for sudoers files
    assert stat.S_IMODE(sudoers.stat().st_mode) == 0o440


def package_box(tmp_path, monkeypatch, path, settings):
    """Runs the tasks that build the box from a volume image, returns the bootstrap info and the commands"""
    info = vagrant_info(tmp_path, path, settings)
    os.makedirs(info.workspace)
    info.volume.image_path = os.path.join(info.workspace, 'volume.' + info.volume.extension)
    with open(info.volume.image_path, 'wb') as volume_image:
        volume_image.write(b'volume')
    tasks.CheckBoxPath.run(info)
    tasks.CreateVagrantBoxDir.run(info)
    calls = record_commands(monkeypatch)
    tasks.PackageBox.run(info)
    return info, [command for command, stdin in calls]


def assert_box_contents(info, commands, disk_name, files):
    folder = info._vagrant['folder']
    [link, tar] = commands
    assert link == ['ln', '-s', info.volume.image_path, os.path.join(folder, disk_name)]
    assert tar[:8] == ['tar', '--create', '--gzip', '--dereference',
                       '--file', info._vagrant['box_path'], '--directory', folder]
    assert sorted(tar[8:]) == files
    with open(os.path.join(folder, 'Vagrantfile'), encoding='utf-8') as vagrantfile:
        assert re.search(r'config\.vm\.base_mac = "080027[0-9A-F]{6}"', vagrantfile.read())


def test_virtualbox_box(tmp_path, monkeypatch):
    info, commands = package_box(tmp_path, monkeypatch, virtualbox_example, {'provider': 'virtualbox'})
    assert_box_contents(info, commands, 'box-disk1.vmdk',
                        ['Vagrantfile', 'box-disk1.vmdk', 'box.ovf', 'metadata.json'])
    folder = info._vagrant['folder']
    with open(os.path.join(folder, 'metadata.json'), encoding='utf-8') as metadata:
        assert json.load(metadata) == {'provider': 'virtualbox'}

    ovf = ET.parse(os.path.join(folder, 'box.ovf')).getroot()
    [disk_file] = ovf.findall('./ovf:References/ovf:File', OVF_NAMESPACES)
    assert disk_file.get('{%(ovf)s}href' % OVF_NAMESPACES) == 'box-disk1.vmdk'
    [disk] = ovf.findall('./ovf:DiskSection/ovf:Disk', OVF_NAMESPACES)
    assert disk.get('{%(ovf)s}capacity' % OVF_NAMESPACES) == str(2 * 1024 ** 3)
    assert disk.get('{%(ovf)s}format' % OVF_NAMESPACES) == 'http://www.vmware.com/specifications/vmdk.html#sparse'
    # The SATA disk of the machine is the disk of the box
    [attached] = ovf.findall('.//vbox:Machine/ovf:StorageControllers/ovf:StorageController'
                             '/ovf:AttachedDevice/ovf:Image', OVF_NAMESPACES)
    assert attached.get('uuid') == '{' + disk.get('{%(vbox)s}uuid' % OVF_NAMESPACES) + '}'
    [os_type] = ovf.findall('./ovf:VirtualSystem/ovf:OperatingSystemSection/vbox:OSType', OVF_NAMESPACES)
    assert os_type.text == 'Debian_64'


def test_libvirt_box(tmp_path, monkeypatch):
    info, commands = package_box(tmp_path, monkeypatch, kvm_example, {'provider': 'libvirt'})
    assert_box_contents(info, commands, 'box.img', ['Vagrantfile', 'box.img', 'metadata.json'])
    with open(os.path.join(info._vagrant['folder'], 'metadata.json'), encoding='utf-8') as metadata:
        assert json.load(metadata) == {'provider': 'libvirt', 'format': 'qcow2', 'virtual_size': 2}


def test_removing_the_box_directory_keeps_the_volume(tmp_path, monkeypatch):
    info, _ = package_box(tmp_path, monkeypatch, virtualbox_example, {})
    folder = info._vagrant['folder']
    tasks.RemoveVagrantBoxDir.run(info)
    assert not os.path.exists(folder)
    # volume.Delete removes the image afterwards and fails when it is gone already
    with open(info.volume.image_path, 'rb') as volume_image:
        assert volume_image.read() == b'volume'
