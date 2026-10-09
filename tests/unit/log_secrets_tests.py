import io
import logging
import os.path
import subprocess
from unittest import mock

import boto3
import pytest
from botocore.awsrequest import AWSResponse

from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import create_list, get_all_tasks, load_tasks
from bootstrapvz.common.tools import load_data, log_check_call
from bootstrapvz.plugins import debconf
from bootstrapvz.plugins.admin_user.tasks import AdminUserPassword
from bootstrapvz.plugins.debconf.tasks import DebconfSetSelections
from bootstrapvz.plugins.ec2_launch.tasks import LaunchEC2Instance
from bootstrapvz.plugins.ec2_publish.tasks import CopyAmiToRegions, PublishAmi, PublishAmiManifest
from bootstrapvz.plugins.root_password.tasks import SetRootPassword
from bootstrapvz.providers.ec2.tasks.ami import AMIName, RegisterAMI
from bootstrapvz.providers.ec2.tasks.connection import Connect, GetCredentials, SilenceBotoDebug

ec2_example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                           '../../manifests/examples/ec2/ebs-unstable-amd64-pvm.yml')

SECRET = 's3cr3t'
# The loggers that boto3 logs through: its own, botocore, s3transfer for S3 transfers
# and urllib3, which botocore sends its HTTP requests with
BOTO_LOGGERS = ['boto3', 'botocore', 's3transfer', 'urllib3']

TEMPORARY_SECRET = 'temporary-s3cr3t'
TEMPORARY_TOKEN = 'temporary-t0ken'
ASSUME_ROLE_RESPONSE = '''<AssumeRoleResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">
  <AssumeRoleResult>
    <Credentials>
      <AccessKeyId>ASIAEXAMPLE</AccessKeyId>
      <SecretAccessKey>{secret}</SecretAccessKey>
      <SessionToken>{token}</SessionToken>
      <Expiration>2030-01-01T00:00:00Z</Expiration>
    </Credentials>
    <AssumedRoleUser>
      <AssumedRoleId>AROAEXAMPLE:bootstrap-vz</AssumedRoleId>
      <Arn>arn:aws:sts::123456789012:assumed-role/build/bootstrap-vz</Arn>
    </AssumedRoleUser>
  </AssumeRoleResult>
  <ResponseMetadata><RequestId>c6104cbe-af31-11e0-8154-cbc7ccf896c7</RequestId></ResponseMetadata>
</AssumeRoleResponse>'''.format(secret=TEMPORARY_SECRET, token=TEMPORARY_TOKEN).encode('utf-8')


@pytest.fixture(name='process')
def fixture_process(monkeypatch):
    """Replaces subprocess.Popen, so that no command runs, and returns the process it hands out"""
    process = mock.MagicMock(stdout=io.StringIO(), stderr=io.StringIO(), returncode=0)
    popen = mock.MagicMock()
    popen.return_value.__enter__.return_value = process
    monkeypatch.setattr(subprocess, 'Popen', popen)
    return process


@pytest.fixture(name='boto_loggers')
def fixture_boto_loggers():
    """Restores the levels of the boto loggers, which SilenceBotoDebug changes for the whole process"""
    loggers = [logging.getLogger(name) for name in BOTO_LOGGERS]
    levels = [logger.level for logger in loggers]
    yield
    for logger, level in zip(loggers, levels):
        logger.setLevel(level)


def plugin_info(plugins):
    return DictClass(root='/target', manifest=DictClass(plugins=plugins))


def set_root_password():
    SetRootPassword.run(plugin_info({'root_password': {'password': SECRET}}))


def set_crypted_root_password():
    SetRootPassword.run(plugin_info({'root_password': {'password-crypted': '$6$salt$' + SECRET}}))


def set_admin_user_password():
    AdminUserPassword.run(plugin_info({'admin_user': {'username': 'admin', 'password': SECRET}}))


def set_debconf_selections():
    DebconfSetSelections.run(plugin_info({'debconf': 'd-i passwd/root-password password ' + SECRET}))


def validate_debconf_selections():
    data = {'plugins': {'debconf': 'd-i passwd/root-password password ' + SECRET}}
    debconf.validate_manifest(data, lambda data, schema_path: None, None)


@pytest.mark.parametrize('call', [set_root_password,
                                  set_crypted_root_password,
                                  set_admin_user_password,
                                  set_debconf_selections,
                                  validate_debconf_selections,
                                  ])
def test_secret_stdin_is_not_logged(caplog, process, call):
    caplog.set_level(logging.DEBUG)
    call()
    # The command still gets the secret, only the log does not
    process.stdin.write.assert_called_once()
    assert SECRET in process.stdin.write.call_args.args[0]
    assert 'Executing: ' in caplog.text
    assert SECRET not in caplog.text


def test_ordinary_stdin_is_logged(caplog, process):
    caplog.set_level(logging.DEBUG)
    log_check_call(['dmsetup', 'create', 'example'], '0 2048 linear /dev/loop0 0')
    process.stdin.write.assert_called_once_with('0 2048 linear /dev/loop0 0\n')
    assert '  stdin: 0 2048 linear /dev/loop0 0' in caplog.text


@pytest.mark.usefixtures('boto_loggers')
@pytest.mark.parametrize('name', ['boto3.resources.factory', 'botocore.parsers',
                                  's3transfer.tasks', 'urllib3.connectionpool'])
def test_boto_debug_is_silenced(caplog, name):
    # bootstrap-vz lets every record through to the handlers, the DEBUG file log among them
    caplog.set_level(logging.DEBUG)
    SilenceBotoDebug.run(None)
    assert not logging.getLogger(name).isEnabledFor(logging.DEBUG)
    assert logging.getLogger(name).isEnabledFor(logging.INFO)


@pytest.mark.usefixtures('boto_loggers')
def test_temporary_credentials_are_not_logged(caplog, monkeypatch, tmp_path):
    # Keep the AWS configuration of the host out of the test
    monkeypatch.setenv('AWS_CONFIG_FILE', str(tmp_path / 'config'))
    monkeypatch.setenv('AWS_SHARED_CREDENTIALS_FILE', str(tmp_path / 'credentials'))
    monkeypatch.delenv('AWS_PROFILE', raising=False)
    caplog.set_level(logging.DEBUG)
    SilenceBotoDebug.run(None)

    class Raw:
        @staticmethod
        def stream(**kwargs):
            yield ASSUME_ROLE_RESPONSE

    def respond(request, **kwargs):
        # Answer instead of sending the request, the way STS answers AssumeRole
        return AWSResponse(request.url, 200, {}, Raw())

    session = boto3.Session(aws_access_key_id='AKIAEXAMPLE', aws_secret_access_key='static-key',
                            region_name='us-east-1')
    sts = session.client('sts')
    sts.meta.events.register('before-send', respond)
    response = sts.assume_role(RoleArn='arn:aws:iam::123456789012:role/build', RoleSessionName='bootstrap-vz')

    assert response['Credentials']['SecretAccessKey'] == TEMPORARY_SECRET
    assert TEMPORARY_SECRET not in caplog.text
    assert TEMPORARY_TOKEN not in caplog.text


def ec2_task_list():
    data = load_data(ec2_example)
    data['plugins']['ec2_publish'] = {'regions': ['eu-west-1'],
                                      'manifest_url': 'https://bucket.s3.amazonaws.com/amis.json',
                                      'public': True}
    data['plugins']['ec2_launch'] = {}
    manifest = Manifest(path=ec2_example, data=data)
    all_tasks = set(get_all_tasks([manifest.modules['provider']] + manifest.modules['plugins']))
    return create_list(load_tasks('resolve_tasks', manifest), all_tasks)


# Every task that calls AWS through boto3, including those of the ec2_publish and ec2_launch plugins
@pytest.mark.parametrize('task', [GetCredentials, Connect, AMIName, RegisterAMI,
                                  CopyAmiToRegions, PublishAmiManifest, PublishAmi, LaunchEC2Instance])
def test_boto_is_silenced_before_it_talks_to_aws(task):
    task_list = ec2_task_list()
    assert task_list.index(SilenceBotoDebug) < task_list.index(task)
