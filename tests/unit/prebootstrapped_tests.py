import contextlib
import logging
import os.path
import shutil
import subprocess
import boto3
import pytest
from botocore.stub import Stubber
from bootstrapvz.base.bootstrapinfo import BootstrapInformation, DictClass
from bootstrapvz.base.fs import load_volume
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common import tools
from bootstrapvz.common.tasks import bootstrap, folder, loopback, volume
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.prebootstrapped import tasks
from bootstrapvz.providers.ec2.tasks import ebs

examples = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests/examples')
example = os.path.join(examples, 'ec2/ebs-unstable-amd64-pvm.yml')
# A raw volume with a single partition
image_example = os.path.join(examples, 'kvm/trixie-openvox.yaml')
folder_example = os.path.join(examples, 'docker/stretch.yml')
real_copyfile = shutil.copyfile

SNAPSHOT_ID = 'snap-0123456789abcdef0'
VOLUME_ID = 'vol-0123456789abcdef0'
INSTANCE_ID = 'i-0123456789abcdef0'
ZONE = 'eu-west-1a'


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    """Fails the test instead of running a command that the test did not mock"""
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {args}'.format(args=args or kwargs))
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)


@pytest.fixture(autouse=True)
def no_aws_config(monkeypatch):
    # Keep the host's AWS profile and config files out of the boto3 session
    monkeypatch.delenv('AWS_PROFILE', raising=False)
    monkeypatch.delenv('AWS_DEFAULT_PROFILE', raising=False)
    monkeypatch.setenv('AWS_CONFIG_FILE', os.devnull)
    monkeypatch.setenv('AWS_SHARED_CREDENTIALS_FILE', os.devnull)


def bootstrap_info(settings):
    data = load_data(example)
    data['plugins']['prebootstrapped'] = settings
    manifest = Manifest(path=example, data=data)
    session = boto3.session.Session(region_name='eu-west-1',
                                    aws_access_key_id='testing', aws_secret_access_key='testing')
    connection = session.client('ec2')
    return DictClass(manifest=manifest,
                     volume=load_volume(manifest.volume, manifest.system['bootloader']),
                     _ec2={'connection': connection,
                           'host': {'availabilityZone': ZONE, 'instanceId': INSTANCE_ID}})


def test_snapshot_logs_snapshot_id(monkeypatch, caplog):
    info = bootstrap_info({})
    # The EBS volume as ebs.Create leaves it
    info.volume.conn = info._ec2['connection']
    info.volume.vol_id = VOLUME_ID
    # Unmounting the volume would call umount on the host
    monkeypatch.setattr(tasks, 'unmounted', lambda vol: contextlib.nullcontext())
    with Stubber(info._ec2['connection']) as stubber:
        stubber.add_response('create_snapshot', {'SnapshotId': SNAPSHOT_ID}, {'VolumeId': VOLUME_ID})
        stubber.add_response('describe_snapshots',
                             {'Snapshots': [{'SnapshotId': SNAPSHOT_ID, 'State': 'completed'}]})
        with caplog.at_level(logging.INFO):
            tasks.Snapshot.run(info)
        stubber.assert_no_pending_responses()
    assert SNAPSHOT_ID in caplog.text


def test_create_from_snapshot_then_attach_detach_and_delete(monkeypatch):
    info = bootstrap_info({'snapshot': SNAPSHOT_ID})
    # Pretend that no /dev/xvd* device exists on the host
    exists = os.path.exists
    monkeypatch.setattr(os.path, 'exists', lambda path: not str(path).startswith('/dev/xvd') and exists(path))
    with Stubber(info._ec2['connection']) as stubber:
        stubber.add_response('create_volume', {'VolumeId': VOLUME_ID},
                             {'Size': 8, 'AvailabilityZone': ZONE,
                              'SnapshotId': SNAPSHOT_ID, 'VolumeType': 'gp2'})
        stubber.add_response('describe_volumes', {'Volumes': [{'VolumeId': VOLUME_ID, 'State': 'available'}]})
        tasks.CreateFromSnapshot.run(info)
        assert info.volume.fsm.current == 'detached'
        assert info.volume.partition_map.root.fsm.current == 'formatted'

        attachment = {'VolumeId': VOLUME_ID, 'InstanceId': INSTANCE_ID, 'Device': '/dev/sdf'}
        stubber.add_response('attach_volume', {}, attachment)
        stubber.add_response('describe_volumes', {'Volumes': [{'VolumeId': VOLUME_ID, 'State': 'in-use'}]})
        ebs.Attach.run(info)
        assert info.volume.device_path == '/dev/xvdf'

        stubber.add_response('detach_volume', {}, attachment)
        stubber.add_response('describe_volumes', {'Volumes': [{'VolumeId': VOLUME_ID, 'State': 'available'}]})
        volume.Detach.run(info)

        # The counter task of CreateFromSnapshot when rolling back
        stubber.add_response('delete_volume', {}, {'VolumeId': VOLUME_ID})
        volume.Delete.run(info)
        stubber.assert_no_pending_responses()
    assert info.volume.fsm.current == 'deleted'


def prebootstrapped_info(path, settings, workspace):
    data = load_data(path)
    data['bootstrapper']['workspace'] = str(workspace)
    data['plugins'] = {'prebootstrapped': settings}
    return BootstrapInformation(Manifest(path=path, data=data))


def record_commands(monkeypatch):
    commands = []

    def log_check_call(command):
        commands.append(command)
        return []
    monkeypatch.setattr(tasks, 'log_check_call', log_check_call)
    return commands


@pytest.mark.parametrize('path, settings, backup, restore, create', [
    (image_example, {'image': '/srv/volume.raw.backup'}, tasks.CopyImage, tasks.CreateFromImage, loopback.Create),
    (folder_example, {'folder': '/srv/root.chroot.backup'}, tasks.CopyFolder, tasks.CreateFromFolder, folder.Create),
], ids=['image', 'folder'])
def test_restoring_a_copy_skips_the_bootstrap(tmp_path, path, settings, backup, restore, create):
    resolved = load_tasks('resolve_tasks', prebootstrapped_info(path, {}, tmp_path).manifest)
    assert {backup, create, bootstrap.Bootstrap} <= resolved
    resolved = load_tasks('resolve_tasks', prebootstrapped_info(path, settings, tmp_path).manifest)
    assert restore in resolved
    assert not {backup, create, bootstrap.Bootstrap} & resolved


def test_copy_of_the_bootstrapped_image_is_kept(tmp_path, monkeypatch, caplog):
    info = prebootstrapped_info(image_example, {}, tmp_path)
    os.makedirs(info.workspace)
    info.volume.image_path = os.path.join(info.workspace, 'volume.raw')
    with open(info.volume.image_path, 'wb') as image:
        image.write(b'bootstrapped')
    events = []

    @contextlib.contextmanager
    def unmounted(vol):
        assert vol is info.volume
        events.append('unmount')
        yield
        events.append('mount')

    def copyfile(src, dst):
        events.append('copy')
        return real_copyfile(src, dst)
    monkeypatch.setattr(tasks, 'unmounted', unmounted)
    monkeypatch.setattr(tasks, 'copyfile', copyfile)

    with caplog.at_level(logging.INFO):
        tasks.CopyImage.run(info)
    # The filesystems are not mounted while the image is copied
    assert events == ['unmount', 'copy', 'mount']
    backup = tmp_path / 'volume-{run_id}.raw.backup'.format(run_id=info.run_id)
    assert backup.read_bytes() == b'bootstrapped'
    assert str(backup) in caplog.text


def test_volume_is_restored_from_the_image_copy(tmp_path):
    backup = tmp_path / 'volume.raw.backup'
    backup.write_bytes(b'bootstrapped')
    info = prebootstrapped_info(image_example, {'image': str(backup)}, tmp_path)
    os.makedirs(info.workspace)
    tasks.CreateFromImage.run(info)
    assert info.volume.image_path == os.path.join(info.workspace, 'volume.raw')
    with open(info.volume.image_path, 'rb') as image:
        assert image.read() == b'bootstrapped'
    # The copy can be used for the next build
    assert backup.read_bytes() == b'bootstrapped'
    # The volume is attached and mounted next, without partitioning or formatting it again
    assert info.volume.fsm.current == 'detached'
    assert info.volume.partition_map.fsm.current == 'unmapped'
    assert [partition.fsm.current for partition in info.volume.partition_map.partitions] == ['unmapped_fmt']


def test_copy_of_the_bootstrapped_folder_is_kept(tmp_path, monkeypatch, caplog):
    info = prebootstrapped_info(folder_example, {}, tmp_path)
    info.volume.path = os.path.join(info.workspace, 'root')
    commands = record_commands(monkeypatch)
    with caplog.at_level(logging.INFO):
        tasks.CopyFolder.run(info)
    backup = str(tmp_path / '{run_id}.chroot.backup'.format(run_id=info.run_id))
    assert commands == [['cp', '-a', info.volume.path, backup]]
    assert backup in caplog.text


def test_folder_is_restored_from_the_copy(tmp_path, monkeypatch):
    backup = str(tmp_path / 'root.chroot.backup')
    info = prebootstrapped_info(folder_example, {'folder': backup}, tmp_path)
    commands = record_commands(monkeypatch)
    tasks.CreateFromFolder.run(info)
    root = os.path.join(info.workspace, 'root')
    assert commands == [['cp', '-a', backup, root]]
    assert info.root == root
    assert info.volume.path == root
    assert info.volume.fsm.current == 'attached'
