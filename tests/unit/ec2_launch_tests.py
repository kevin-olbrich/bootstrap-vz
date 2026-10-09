import os
import boto3
import pytest
from botocore.stub import Stubber
from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.plugins.ec2_launch import tasks

IMAGE_ID = 'ami-0123456789abcdef0'
SNAPSHOT_ID = 'snap-0123456789abcdef0'
INSTANCE_ID = 'i-0123456789abcdef0'


@pytest.fixture(autouse=True)
def no_host_aws_config(monkeypatch):
    # Keep the client away from the host's AWS profile and configuration files
    monkeypatch.delenv('AWS_PROFILE', raising=False)
    monkeypatch.delenv('AWS_DEFAULT_PROFILE', raising=False)
    monkeypatch.setenv('AWS_CONFIG_FILE', os.devnull)
    monkeypatch.setenv('AWS_SHARED_CREDENTIALS_FILE', os.devnull)


def ec2_info():
    # The fake credentials keep the client away from the host's, and the Stubber
    # answers every call, so nothing goes over the network
    connection = boto3.client('ec2', region_name='us-east-1',
                              aws_access_key_id='testing', aws_secret_access_key='testing')
    # What RegisterAMI, Snapshot and LaunchEC2Instance leave behind
    return DictClass(_ec2={'connection': connection,
                           'image': {'ImageId': IMAGE_ID},
                           'snapshot': SNAPSHOT_ID,
                           'instance': {'InstanceId': INSTANCE_ID, 'State': {'Name': 'pending'}}})


def instance_state(name):
    return {'Reservations': [{'Instances': [{'InstanceId': INSTANCE_ID, 'State': {'Name': name}}]}]}


def test_deregister_ami_deletes_image_and_snapshot_once_instance_runs():
    info = ec2_info()
    with Stubber(info._ec2['connection']) as stubber:
        stubber.add_response('describe_instances', instance_state('running'), {'InstanceIds': [INSTANCE_ID]})
        stubber.add_response('deregister_image', {}, {'ImageId': IMAGE_ID})
        stubber.add_response('delete_snapshot', {}, {'SnapshotId': SNAPSHOT_ID})
        tasks.DeregisterAMI.run(info)
        stubber.assert_no_pending_responses()


def test_deregister_ami_keeps_image_and_snapshot_if_instance_does_not_run(caplog):
    info = ec2_info()
    # Any call beyond the queued response fails the test with UnStubbedResponseError
    with Stubber(info._ec2['connection']) as stubber:
        stubber.add_response('describe_instances', instance_state('terminated'),
                             {'InstanceIds': [INSTANCE_ID]})
        tasks.DeregisterAMI.run(info)
        stubber.assert_no_pending_responses()
    assert 'keeping the AMI' in caplog.text
