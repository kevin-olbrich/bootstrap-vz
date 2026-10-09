import contextlib
import logging
import os.path
import boto3
import pytest
from botocore.stub import Stubber
from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.base.fs import load_volume
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.common.tasks import volume
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.prebootstrapped import tasks
from bootstrapvz.providers.ec2.tasks import ebs

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/ec2/ebs-unstable-amd64-pvm.yml')

SNAPSHOT_ID = 'snap-0123456789abcdef0'
VOLUME_ID = 'vol-0123456789abcdef0'
INSTANCE_ID = 'i-0123456789abcdef0'
ZONE = 'eu-west-1a'


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
