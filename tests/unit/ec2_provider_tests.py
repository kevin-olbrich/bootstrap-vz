import io
import json
import os.path
import stat
import subprocess
import urllib.request

import boto3
import pytest
from botocore.awsrequest import AWSResponse
from botocore.httpsession import URLLib3Session
from botocore.stub import Stubber

from bootstrapvz.base.bootstrapinfo import BootstrapInformation, DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import create_list, get_all_tasks, load_tasks
from bootstrapvz.common import tools
from bootstrapvz.common.exceptions import TaskError
from bootstrapvz.common.tasks import grub
from bootstrapvz.common.tools import load_data
from bootstrapvz.providers.ec2.tasks import (ami, assets, boot, connection, ebs, filesystem, host, network,
                                             packages, tuning)

manifests = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests')
hvm_example = os.path.join(manifests, 'official/ec2/ebs-stretch-amd64-hvm.yml')
pvm_example = os.path.join(manifests, 'official/ec2/ebs-wheezy-amd64-pvm.yml')
s3_example = os.path.join(manifests, 'examples/ec2/s3-wheezy-amd64-pvm.yml')

REGION = 'eu-central-1'
ZONE = 'eu-central-1b'
INSTANCE_ID = 'i-0123456789abcdef0'
VOLUME_ID = 'vol-0123456789abcdef0'
SNAPSHOT_ID = 'snap-0123456789abcdef0'
IMAGE_ID = 'ami-0123456789abcdef0'
TAGS = [{'Key': 'Name', 'Value': 'debian-stretch'}]


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command or opens a URL: {args}'.format(args=args))
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)
    monkeypatch.setattr(urllib.request, 'urlopen', refuse)
    # A boto3 call that neither a Stubber nor a before-send handler answers must not reach AWS
    monkeypatch.setattr(URLLib3Session, 'send', refuse)
    # Keep the AWS configuration, credentials and instance metadata of the host out of boto3
    for name in [name for name in os.environ if name.startswith('AWS_')]:
        monkeypatch.delenv(name)
    monkeypatch.setenv('AWS_CONFIG_FILE', os.devnull)
    monkeypatch.setenv('AWS_SHARED_CREDENTIALS_FILE', os.devnull)
    # Without BOTO_CONFIG, botocore reads credentials from the legacy /etc/boto.cfg and ~/.boto
    monkeypatch.setenv('BOTO_CONFIG', os.devnull)
    monkeypatch.setenv('AWS_EC2_METADATA_DISABLED', 'true')


def bootstrap_info(root, path=hvm_example, data=None):
    # A DictClass, because the namespace of the provider (info._ec2) only exists at runtime
    info = DictClass(vars(BootstrapInformation(Manifest(path=path, data=data or load_data(path)))))
    info.root = str(root)
    return info


def ec2_client():
    # The fake credentials keep the client away from the host's, the Stubber answers every call
    return boto3.client('ec2', region_name=REGION, aws_access_key_id='testing', aws_secret_access_key='testing')


def read_lines(path):
    with open(path, encoding='utf-8') as lines:
        return lines.read().splitlines()


def test_instance_metadata_gives_region_zone_and_instance(tmp_path, monkeypatch):
    document = {'region': REGION, 'availabilityZone': ZONE, 'instanceId': INSTANCE_ID,
                'architecture': 'x86_64', 'accountId': '123456789012'}
    timeouts = []

    def urlopen(url, data=None, timeout=None, **kwargs):
        request = url if isinstance(url, urllib.request.Request) else urllib.request.Request(url, data)
        timeouts.append(timeout)
        if request.get_method() == 'PUT' and request.full_url == 'http://169.254.169.254/latest/api/token':
            # IMDSv2 clients ask for a session token first
            return io.BytesIO(b'session-token')
        assert request.get_method() == 'GET'
        assert request.full_url == 'http://169.254.169.254/latest/dynamic/instance-identity/document'
        return io.BytesIO(json.dumps(document).encode('utf-8'))
    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    info = bootstrap_info(tmp_path)
    host.GetInstanceMetadata.run(info)
    assert info._ec2['region'] == REGION
    assert info._ec2['host']['availabilityZone'] == ZONE
    assert info._ec2['host']['instanceId'] == INSTANCE_ID
    # A build host outside of EC2 must not wait forever
    assert timeouts and None not in timeouts


def test_ebs_build_needs_no_bundle_tools(tmp_path):
    info = bootstrap_info(tmp_path)
    host.AddExternalCommands.run(info)
    assert not info.host_dependencies


def credentials(root, provider=None):
    data = load_data(hvm_example)
    data['provider'].update(provider or {})
    info = bootstrap_info(root, data=data)
    connection.GetCredentials.run(info)
    return info.credentials


def test_manifest_credentials_take_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv('AWS_ACCESS_KEY', 'AKIDENVIRONMENT')
    monkeypatch.setenv('AWS_SECRET_KEY', 'environment-secret')
    keys = {'access-key': 'AKIDMANIFEST', 'secret-key': 'manifest-secret'}
    assert credentials(tmp_path, {'credentials': keys}) == keys


def test_credentials_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('AWS_ACCESS_KEY', 'AKIDENVIRONMENT')
    monkeypatch.setenv('AWS_SECRET_KEY', 'environment-secret')
    assert credentials(tmp_path) == {'access-key': 'AKIDENVIRONMENT', 'secret-key': 'environment-secret'}


def test_credentials_from_a_profile(tmp_path, monkeypatch):
    shared_credentials = os.path.join(tmp_path, 'credentials')
    with open(shared_credentials, 'w', encoding='utf-8') as config:
        config.write('[build]\n'
                     'aws_access_key_id = AKIDPROFILE\n'
                     'aws_secret_access_key = profile-secret\n'
                     'aws_session_token = profile-token\n')
    monkeypatch.setenv('AWS_SHARED_CREDENTIALS_FILE', shared_credentials)
    assert credentials(tmp_path, {'profile': 'build'}) == {'access-key': 'AKIDPROFILE',
                                                           'secret-key': 'profile-secret',
                                                           'security-token': 'profile-token'}


@pytest.mark.parametrize('provider, message', [({'profile': 'missing'}, 'Profile specified was not found'),
                                               ({}, 'No ec2 credentials found')],
                         ids=['unknown-profile', 'none'])
def test_missing_credentials_fail(tmp_path, provider, message):
    with pytest.raises(RuntimeError, match=message):
        credentials(tmp_path, provider)


class Body:
    """The raw body of a botocore response"""
    def __init__(self, content):
        self.content = content

    def stream(self, **kwargs):
        yield self.content


def test_connection_signs_requests_for_the_build_region(tmp_path):
    info = bootstrap_info(tmp_path)
    info.credentials = {'access-key': 'AKIDEXAMPLE', 'secret-key': 'secret', 'security-token': 'session-token'}
    info._ec2['region'] = REGION
    connection.Connect.run(info)
    requests = []

    def respond(request, **kwargs):
        # Answer instead of sending the request
        requests.append(request)
        body = b'<DescribeImagesResponse xmlns="http://ec2.amazonaws.com/doc/2016-11-15/"><imagesSet/>' \
               b'</DescribeImagesResponse>'
        return AWSResponse(request.url, 200, {}, Body(body))
    info._ec2['connection'].meta.events.register('before-send', respond)
    info._ec2['connection'].describe_images(Owners=['self'])
    [request] = requests
    assert request.url == 'https://ec2.{region}.amazonaws.com/'.format(region=REGION)
    authorization = request.headers['Authorization'].decode('ascii')
    assert 'Credential=AKIDEXAMPLE/' in authorization
    assert '/{region}/ec2/aws4_request'.format(region=REGION) in authorization
    assert request.headers['X-Amz-Security-Token'] == b'session-token'


def ami_name_info(root):
    data = load_data(hvm_example)
    data['name'] = 'debian-{system.release}-{system.architecture}'
    data['provider']['description'] = 'Debian {system.release}'
    info = bootstrap_info(root, data=data)
    info._ec2['connection'] = ec2_client()
    return info


def test_ami_name_and_description_come_from_the_manifest(tmp_path):
    info = ami_name_info(tmp_path)
    images = [{'ImageId': 'ami-1', 'Name': 'debian-jessie-amd64'}, {'ImageId': 'ami-2'}]
    with Stubber(info._ec2['connection']) as stubber:
        stubber.add_response('describe_images', {'Images': images}, {'Owners': ['self']})
        ami.AMIName.run(info)
        stubber.assert_no_pending_responses()
    assert info._ec2['ami_name'] == 'debian-stretch-amd64'
    assert info._ec2['ami_description'] == 'Debian stretch'


def test_existing_ami_name_fails_before_the_build(tmp_path):
    info = ami_name_info(tmp_path)
    images = [{'ImageId': IMAGE_ID, 'Name': 'debian-stretch-amd64'}]
    with Stubber(info._ec2['connection']) as stubber:
        stubber.add_response('describe_images', {'Images': images}, {'Owners': ['self']})
        with pytest.raises(TaskError, match='debian-stretch-amd64 already exists'):
            ami.AMIName.run(info)


@pytest.mark.parametrize('tags, provider, extra_params', [
    (None, {}, {}),
    ({'Name': 'debian-{system.release}'}, {'encrypted': True, 'kms_key_id': 'alias/images'},
     {'TagSpecifications': [{'ResourceType': 'volume', 'Tags': TAGS}],
      'Encrypted': True, 'KmsKeyId': 'alias/images'}),
], ids=['plain', 'tagged-encrypted'])
def test_volume_is_created_in_the_zone_of_the_build_host(tmp_path, tags, provider, extra_params):
    data = load_data(hvm_example)
    data['provider'].update(provider)
    del data['tags']
    if tags:
        data['tags'] = tags
    info = bootstrap_info(tmp_path, data=data)
    info._ec2.update(connection=ec2_client(), host={'availabilityZone': ZONE, 'instanceId': INSTANCE_ID})
    params = {'Size': 8, 'AvailabilityZone': ZONE, 'VolumeType': 'gp2'}
    params.update(extra_params)
    with Stubber(info._ec2['connection']) as stubber:
        stubber.add_response('create_volume', {'VolumeId': VOLUME_ID}, params)
        stubber.add_response('describe_volumes', {'Volumes': [{'VolumeId': VOLUME_ID, 'State': 'available'}]},
                             {'VolumeIds': [VOLUME_ID], 'Filters': [{'Name': 'status', 'Values': ['available']}]})
        ebs.Create.run(info)
        stubber.assert_no_pending_responses()
    assert info.volume.fsm.current == 'detached'


def test_snapshot_is_tagged(tmp_path):
    data = load_data(hvm_example)
    data['tags'] = {'Name': 'debian-{system.release}'}
    info = bootstrap_info(tmp_path, data=data)
    info._ec2['connection'] = ec2_client()
    # The EBS volume as ebs.Create leaves it
    info.volume.conn = info._ec2['connection']
    info.volume.vol_id = VOLUME_ID
    with Stubber(info._ec2['connection']) as stubber:
        stubber.add_response('create_snapshot', {'SnapshotId': SNAPSHOT_ID}, {'VolumeId': VOLUME_ID})
        stubber.add_response('describe_snapshots',
                             {'Snapshots': [{'SnapshotId': SNAPSHOT_ID, 'State': 'completed'}]})
        stubber.add_response('create_tags', {}, {'Resources': [SNAPSHOT_ID], 'Tags': TAGS})
        ebs.Snapshot.run(info)
        stubber.assert_no_pending_responses()
    assert info._ec2['snapshot'] == SNAPSHOT_ID


def test_hvm_ami_is_registered_from_the_snapshot(tmp_path):
    data = load_data(hvm_example)
    data['tags'] = {'Name': 'debian-{system.release}'}
    info = bootstrap_info(tmp_path, data=data)
    info._ec2.update(connection=ec2_client(), ami_name='debian-stretch-amd64', ami_description='Debian stretch',
                     snapshot=SNAPSHOT_ID)
    params = {'Name': 'debian-stretch-amd64',
              'Description': 'Debian stretch',
              'Architecture': 'x86_64',
              'RootDeviceName': '/dev/xvda',
              'BlockDeviceMappings': [{'DeviceName': '/dev/xvda',
                                       'Ebs': {'SnapshotId': SNAPSHOT_ID,
                                               'VolumeSize': 8,
                                               'VolumeType': 'gp2',
                                               'DeleteOnTermination': True}}],
              'VirtualizationType': 'hvm',
              # The manifest sets enhanced_networking: simple
              'SriovNetSupport': 'simple',
              'EnaSupport': True}
    with Stubber(info._ec2['connection']) as stubber:
        stubber.add_response('register_image', {'ImageId': IMAGE_ID}, params)
        stubber.add_response('create_tags', {}, {'Resources': [IMAGE_ID], 'Tags': TAGS})
        ami.RegisterAMI.run(info)
        stubber.assert_no_pending_responses()
    assert info._ec2['image']['ImageId'] == IMAGE_ID


@pytest.fixture(name='grub_d')
def fixture_grub_d(tmp_path):
    """The scripts that grub-pc installs to /etc/grub.d"""
    grub_d = os.path.join(tmp_path, 'etc/grub.d')
    os.makedirs(grub_d)
    for name in ['00_header', '10_linux', '40_custom']:
        with open(os.path.join(grub_d, name), 'w', encoding='utf-8') as script:
            script.write('#!/bin/sh\n')
        os.chmod(os.path.join(grub_d, name), 0o755)
    return grub_d


@pytest.mark.parametrize('path, partitions, device, root', [
    (pvm_example, None, 'GRUB_DEVICE=/dev/xvda', '\troot (hd0)'),
    (pvm_example, {'type': 'msdos', 'root': {'filesystem': 'ext4', 'size': '8GiB'}},
     'GRUB_DEVICE=/dev/xvda1', '\troot (hd0,0)'),
    # The root filesystem of an instance store AMI is on the unpartitioned xvda1
    (s3_example, None, 'GRUB_DEVICE=/dev/xvda1', '\troot (hd0)'),
], ids=['ebs-unpartitioned', 'ebs-msdos', 's3'])
def test_pvgrub_menu_boots_the_root_device(tmp_path, grub_d, path, partitions, device, root):
    data = load_data(path)
    if partitions:
        data['volume']['partitions'] = partitions
    boot.CreatePVGrubCustomRule.run(bootstrap_info(tmp_path, path, data))
    # Only the script that writes the menu.lst for pv-grub runs
    for name in ['00_header', '10_linux']:
        mode = os.stat(os.path.join(grub_d, name)).st_mode
        assert not mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        assert mode & stat.S_IRUSR
    custom = os.path.join(grub_d, '40_custom')
    assert stat.S_IMODE(os.stat(custom).st_mode) == 0o755
    lines = read_lines(custom)
    assert device in lines
    assert root in lines
    original = read_lines(os.path.join(assets, 'grub.d/40_custom'))
    assert len(lines) == len(original)
    assert [line for line in lines if line not in (device, root)] == \
        [line for line in original if line not in ('GRUB_DEVICE=/dev/xvda', '\troot (hd0)')]


def test_pvgrub_boot_configuration(tmp_path, grub_d, monkeypatch):
    commands = []

    def log_check_call(command):
        commands.append(command)
        return []
    monkeypatch.setattr(boot, 'log_check_call', log_check_call)
    info = bootstrap_info(tmp_path, pvm_example)
    manifest = info.manifest
    all_tasks = set(get_all_tasks([manifest.modules['provider']] + manifest.modules['plugins']))
    os.makedirs(os.path.join(tmp_path, 'etc/default'))
    # Run the grub and pv-grub tasks in the order of the build
    for task in create_list(load_tasks('resolve_tasks', manifest), all_tasks):
        if task.__module__ in (grub.__name__, boot.__name__):
            task.run(info)
    # With the 40_custom script, update-grub writes grub.cfg in the menu.lst format of pv-grub,
    # and pv-grub reads /boot/grub/menu.lst
    assert commands == [['chroot', str(tmp_path), 'update-grub'],
                        ['chroot', str(tmp_path), 'ln', '--symbolic', '/boot/grub/grub.cfg', '/boot/grub/menu.lst']]
    # Checks the settings written for update-grub, not that update-grub runs after they are written
    settings = dict(line.split('=', 1) for line in read_lines(os.path.join(tmp_path, 'etc/default/grub'))
                    if '=' in line and not line.startswith('#'))
    # Xen PV guests have their console on hvc0
    assert settings['GRUB_CMDLINE_LINUX_DEFAULT'].strip('"').split()[-1] == 'console=hvc0'
    assert {'consoleblank=0', 'elevator=noop'} <= set(settings['GRUB_CMDLINE_LINUX'].strip('"').split())
    assert settings['GRUB_TIMEOUT'] == '0'


def test_instance_store_fstab_mounts_xvda1(tmp_path):
    os.mkdir(os.path.join(tmp_path, 'etc'))
    filesystem.S3FStab.run(bootstrap_info(tmp_path, s3_example))
    assert read_lines(os.path.join(tmp_path, 'etc/fstab')) == ['/dev/xvda1 / ext4 defaults 1 1']


@pytest.mark.parametrize('path, architecture, kernel', [(pvm_example, 'amd64', 'linux-image-amd64'),
                                                        (pvm_example, 'i386', 'linux-image-686'),
                                                        (hvm_example, 'i386', 'linux-image-686-pae')],
                         ids=['wheezy-amd64', 'wheezy-i386', 'stretch-i386'])
def test_default_packages(tmp_path, path, architecture, kernel):
    data = load_data(path)
    data['system']['architecture'] = architecture
    info = bootstrap_info(tmp_path, path, data)
    packages.DefaultPackages.run(info)
    names = {package.name for package in info.packages.remote()}
    # The init scripts call `file' to detect the type of the user-data
    assert 'file' in names
    assert [name for name in names if name.startswith('linux-image-')] == [kernel]


def test_sysctl_tuning(tmp_path):
    os.makedirs(os.path.join(tmp_path, 'etc/sysctl.d'))
    tuning.TuneSystem.run(bootstrap_info(tmp_path))
    tuned = os.path.join(tmp_path, 'etc/sysctl.d/01_ec2.conf')
    assert read_lines(tuned) == read_lines(os.path.join(assets, 'sysctl.d/tuning.conf'))
    assert stat.S_IMODE(os.stat(tuned).st_mode) == 0o644
    assert 'vm.swappiness = 0' in read_lines(tuned)


def test_unused_kernel_modules_are_blacklisted(tmp_path):
    blacklist = os.path.join(tmp_path, 'etc/modprobe.d/blacklist.conf')
    os.makedirs(os.path.dirname(blacklist))
    with open(blacklist, 'w', encoding='utf-8') as config:
        config.write('blacklist pcspkr\n')
    tuning.BlackListModules.run(bootstrap_info(tmp_path))
    assert read_lines(blacklist) == ['blacklist pcspkr', 'blacklist i2c_piix4', 'blacklist psmouse']


def test_wheezy_uses_dhcpcd(tmp_path):
    info = bootstrap_info(tmp_path, pvm_example)
    network.InstallDHCPCD.run(info)
    assert [package.name for package in info.packages.remote()] == ['dhcpcd']
    assert {'isc-dhcp-client', 'isc-dhcp-common'} <= info.exclude_packages


@pytest.mark.parametrize('setting', ["#SET_DNS='yes'", "SET_DNS='no'"])
def test_dhcpcd_sets_the_nameservers(tmp_path, setting):
    defaults = os.path.join(tmp_path, 'etc/default/dhcpcd')
    os.makedirs(os.path.dirname(defaults))
    with open(defaults, 'w', encoding='utf-8') as config:
        config.write('# Configure the DNS\n' + setting + '\nSET_DOMAIN=\'no\'\n')
    network.EnableDHCPCDDNS.run(bootstrap_info(tmp_path, pvm_example))
    assert read_lines(defaults) == ['# Configure the DNS', "SET_DNS='yes'", "SET_DOMAIN='no'"]
