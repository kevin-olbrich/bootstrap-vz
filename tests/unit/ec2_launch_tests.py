import os
import subprocess
import boto3
import pytest
from botocore.stub import Stubber
from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.plugins.ec2_launch import tasks

IMAGE_ID = 'ami-0123456789abcdef0'
SNAPSHOT_ID = 'snap-0123456789abcdef0'
INSTANCE_ID = 'i-0123456789abcdef0'


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    """Fails the test when a command that the test does not mock would run on the host.
    tools.log_call and tools.log_check_call start their commands through subprocess.Popen, so they fail too.
    They are not replaced by name: a module that imports them while the test runs would keep the
    replacement for the rest of the session.
    """
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {args}'.format(args=args or kwargs))
    monkeypatch.setattr(subprocess, 'Popen', refuse)


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


def test_launch_instance_from_registered_ami():
    info = ec2_info()
    del info._ec2['instance']
    info.manifest = DictClass(plugins={'ec2_launch': {'security_group_ids': ['sg-0123456789abcdef0'],
                                                      'ssh_key': 'build-key',
                                                      'instance_type': 't2.micro',
                                                      'tags': {'Name': 'debian-{system.release}'}}})
    info.manifest_vars = {'system': DictClass(release='trixie')}
    with Stubber(info._ec2['connection']) as stubber:
        stubber.add_response('run_instances', {'Instances': [{'InstanceId': INSTANCE_ID}]},
                             {'ImageId': IMAGE_ID, 'MinCount': 1, 'MaxCount': 1,
                              'SecurityGroupIds': ['sg-0123456789abcdef0'], 'KeyName': 'build-key',
                              'InstanceType': 't2.micro'})
        # Manifest variables in the tag values are filled in
        stubber.add_response('create_tags', {},
                             {'Resources': [INSTANCE_ID], 'Tags': [{'Key': 'Name', 'Value': 'debian-trixie'}]})
        tasks.LaunchEC2Instance.run(info)
        stubber.assert_no_pending_responses()
    # PrintPublicIPAddress and DeregisterAMI work on this instance
    assert info._ec2['instance']['InstanceId'] == INSTANCE_ID


def print_public_ip(tmp_path, respond):
    info = ec2_info()
    info.manifest = DictClass(plugins={'ec2_launch': {'print_public_ip': str(tmp_path / 'ip')}})
    with Stubber(info._ec2['connection']) as stubber:
        respond(stubber)
        tasks.PrintPublicIPAddress.run(info)
        stubber.assert_no_pending_responses()
    return (tmp_path / 'ip').read_text(encoding='utf-8')


def test_public_ip_written_once_instance_is_ok(tmp_path):
    def respond(stubber):
        stubber.add_response('describe_instance_status',
                             {'InstanceStatuses': [{'InstanceId': INSTANCE_ID, 'InstanceStatus': {'Status': 'ok'}}]},
                             {'InstanceIds': [INSTANCE_ID],
                              'Filters': [{'Name': 'instance-state-name', 'Values': ['running']}]})
        stubber.add_response('describe_instances',
                             {'Reservations': [{'Instances': [{'InstanceId': INSTANCE_ID,
                                                               'PublicIpAddress': '203.0.113.10'}]}]},
                             {'InstanceIds': [INSTANCE_ID]})
    assert print_public_ip(tmp_path, respond) == '203.0.113.10'


def test_public_ip_file_empty_without_status(tmp_path, caplog):
    def respond(stubber):
        stubber.add_client_error('describe_instance_status', service_error_code='UnauthorizedOperation')
    # The build goes on, whatever reads the file finds no address
    assert print_public_ip(tmp_path, respond) == ''
    assert 'Could not get IP address for the instance' in caplog.text
