import json
import os
import subprocess
import time

import boto3
import pytest
from botocore.stub import Stubber

from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.plugins.ec2_publish import tasks

IMAGE_ID = 'ami-0123456789abcdef0'
COPIES = {'us-east-1': 'ami-0aaaaaaaaaaaaaaa1', 'ap-northeast-1': 'ami-0bbbbbbbbbbbbbbb2'}
CREDENTIALS = {'access-key': 'build-access-key', 'secret-key': 'build-secret-key', 'security-token': 'build-token'}


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
    # Keep the clients away from the host's AWS profile and configuration files
    monkeypatch.delenv('AWS_PROFILE', raising=False)
    monkeypatch.delenv('AWS_DEFAULT_PROFILE', raising=False)
    monkeypatch.setenv('AWS_CONFIG_FILE', os.devnull)
    monkeypatch.setenv('AWS_SHARED_CREDENTIALS_FILE', os.devnull)


def client(region):
    # A Stubber answers every call of these clients, so nothing goes over the network
    return boto3.client('ec2', region_name=region, aws_access_key_id='testing', aws_secret_access_key='testing')


def publish_info(settings):
    # What GetCredentials, Connect, AMIName and RegisterAMI leave behind
    return DictClass(manifest=DictClass(plugins={'ec2_publish': settings}),
                     credentials=CREDENTIALS,
                     _ec2={'connection': client('eu-west-1'), 'region': 'eu-west-1',
                           'image': {'ImageId': IMAGE_ID}, 'ami_name': 'debian-trixie-amd64-20261009'})


def test_ami_copied_to_regions_with_build_credentials(monkeypatch):
    info = publish_info({'regions': ['us-east-1', 'ap-northeast-1']})
    created = []
    stubbers = []
    real_client = boto3.client

    def stubbed_client(service, region_name, **kwargs):
        created.append((service, region_name, kwargs))
        connection = real_client(service, region_name=region_name, **kwargs)
        stubber = Stubber(connection)
        stubber.add_response('copy_image', {'ImageId': COPIES[region_name]},
                             {'SourceRegion': 'eu-west-1', 'SourceImageId': IMAGE_ID,
                              'Name': 'debian-trixie-amd64-20261009',
                              'Description': 'Copied from {ami} (eu-west-1)'.format(ami=IMAGE_ID)})
        stubber.activate()
        stubbers.append(stubber)
        return connection
    monkeypatch.setattr(boto3, 'client', stubbed_client)
    tasks.CopyAmiToRegions.run(info)
    for stubber in stubbers:
        stubber.assert_no_pending_responses()
    credentials = {'aws_access_key_id': CREDENTIALS['access-key'],
                   'aws_secret_access_key': CREDENTIALS['secret-key'],
                   'aws_session_token': CREDENTIALS['security-token']}
    assert created == [('ec2', 'us-east-1', credentials), ('ec2', 'ap-northeast-1', credentials)]
    # PublishAmiManifest and PublishAmi work on the AMIs of all regions, the source region included
    assert info._ec2['region_amis'] == {'eu-west-1': IMAGE_ID, **COPIES}
    assert sorted(info._ec2['region_conns']) == ['ap-northeast-1', 'eu-west-1', 'us-east-1']
    assert info._ec2['region_conns']['eu-west-1'] is info._ec2['connection']


def image(ami, state):
    return {'Images': [{'ImageId': ami, 'State': state}]}


def test_amis_made_public_once_available(monkeypatch):
    info = publish_info({'public': True})
    copy = client('us-east-1')
    info._ec2['region_amis'] = {'eu-west-1': IMAGE_ID, 'us-east-1': COPIES['us-east-1']}
    info._ec2['region_conns'] = {'eu-west-1': info._ec2['connection'], 'us-east-1': copy}
    monkeypatch.setattr(time, 'sleep', lambda seconds: None)
    public = {'Add': [{'Group': 'all'}]}
    with Stubber(info._ec2['connection']) as source_stubber, Stubber(copy) as copy_stubber:
        source_stubber.add_response('describe_images', image(IMAGE_ID, 'available'), {'ImageIds': [IMAGE_ID]})
        source_stubber.add_response('modify_image_attribute', {}, {'ImageId': IMAGE_ID, 'LaunchPermission': public})
        # The copy can only be shared once it is no longer pending
        copy_id = COPIES['us-east-1']
        copy_stubber.add_response('describe_images', image(copy_id, 'pending'), {'ImageIds': [copy_id]})
        copy_stubber.add_response('describe_images', image(copy_id, 'available'), {'ImageIds': [copy_id]})
        copy_stubber.add_response('modify_image_attribute', {}, {'ImageId': copy_id, 'LaunchPermission': public})
        tasks.PublishAmi.run(info)
        source_stubber.assert_no_pending_responses()
        copy_stubber.assert_no_pending_responses()


def test_ami_manifest_written_to_local_file(tmp_path):
    path = tmp_path / 'amis.json'
    info = publish_info({'manifest_url': str(path)})
    info._ec2['region_amis'] = {'eu-west-1': IMAGE_ID, 'us-east-1': COPIES['us-east-1']}
    tasks.PublishAmiManifest.run(info)
    assert json.loads(path.read_text(encoding='utf-8')) == {'eu-west-1': IMAGE_ID, 'us-east-1': COPIES['us-east-1']}


class S3:
    """Records the objects that are uploaded"""
    def __init__(self):
        self.objects = []

    def put_object(self, **params):
        self.objects.append(params)


@pytest.mark.parametrize('url, region, bucket, key', [
    ('https://images.s3-eu-west-1.amazonaws.com/debian/amis.json', 'eu-west-1', 'images', 'debian/amis.json'),
    ('https://s3-eu-west-1.amazonaws.com/images/debian/amis.json', 'eu-west-1', 'images', 'debian/amis.json'),
    ('https://images.s3.amazonaws.com/amis.json', 'us-east-1', 'images', 'amis.json'),
], ids=['virtual-hosted', 'path', 'us-east-1'])
def test_ami_manifest_uploaded_to_s3(monkeypatch, url, region, bucket, key):
    info = publish_info({'manifest_url': url})
    info._ec2['region_amis'] = {'eu-west-1': IMAGE_ID}
    s3 = S3()
    clients = []

    def s3_client(service, region_name, **kwargs):
        clients.append((service, region_name))
        return s3
    monkeypatch.setattr(boto3, 'client', s3_client)
    tasks.PublishAmiManifest.run(info)
    assert clients == [('s3', region)]
    [uploaded] = s3.objects
    assert (uploaded['Bucket'], uploaded['Key'], uploaded['ContentType']) == (bucket, key, 'application/json')
    assert json.loads(uploaded['Body']) == {'eu-west-1': IMAGE_ID}
