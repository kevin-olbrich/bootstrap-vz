import io
import logging
import os.path
import subprocess

import pytest
import requests

from bootstrapvz.base.bootstrapinfo import BootstrapInformation, DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.common import tools
from bootstrapvz.common.tools import load_data
from bootstrapvz.providers.oracle import apiclient
from bootstrapvz.providers.oracle.tasks import api, image, network, packages

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/official/oracle/jessie.yml')

STORAGE = 'https://example.storage.oraclecloud.com'
CONTAINER = STORAGE + '/v1/Storage-example/images/'
TOKEN = 'AUTH_tk0123456789abcdef'
CREDENTIALS = {'username': 'builder', 'password': 'secret', 'identity-domain': 'example'}


class NoNetwork:
    def __getattr__(self, name):
        raise AssertionError('sends an HTTP request with requests.' + name)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {args}'.format(args=args))
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)
    monkeypatch.setattr(apiclient, 'requests', NoNetwork())
    # The API client turns down the log level of these loggers for the whole process
    levels = {name: logging.getLogger(name).level for name in ['requests', 'urllib3']}
    yield
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)


def response(status_code, content=b'', headers=None):
    answer = requests.Response()
    answer.status_code = status_code
    answer.headers.update(headers or {})
    answer.raw = io.BytesIO(content)
    return answer


class Storage:
    """Answers the Swift v1 requests of the API client from memory, like the storage service would"""
    def __init__(self):
        self.objects = {}

    def get(self, url, headers=None, timeout=None, stream=False):
        assert timeout
        if url == STORAGE + '/auth/v1.0':
            if headers == {'X-Storage-User': 'Storage-example:builder', 'X-Storage-Pass': 'secret'}:
                return response(200, headers={'X-Auth-Token': TOKEN})
            return response(401, b'Unauthorized')
        assert headers == {'X-Auth-Token': TOKEN}
        return response(200, self.download(url))

    def put(self, url, data=None, headers=None, timeout=None):
        assert timeout
        assert headers['X-Auth-Token'] == TOKEN
        self.objects[url] = (data, headers)
        return response(201)

    def download(self, url):
        data, headers = self.objects[url]
        if 'X-Object-Manifest' in headers:
            # A large object joins the segments whose names start with the prefix, in name order
            prefix = STORAGE + '/v1/Storage-example/' + headers['X-Object-Manifest']
            return b''.join(self.objects[name][0] for name in sorted(self.objects) if name.startswith(prefix))
        return data


@pytest.fixture(name='storage')
def fixture_storage(monkeypatch):
    storage = Storage()
    monkeypatch.setattr(apiclient, 'requests', storage)
    return storage


def bootstrap_info(root, credentials=None):
    data = load_data(example)
    data['name'] = 'debian-{system.release}'
    data['bootstrapper']['workspace'] = str(root)
    if credentials:
        data['provider'].update(credentials=credentials, container='images', verify=True)
    # A DictClass, because the namespace of the provider (info._oracle) only exists at runtime
    info = DictClass(vars(BootstrapInformation(Manifest(path=example, data=data))))
    info.root = os.path.join(root, 'root')
    return info


def connect(root, tarball_content):
    info = bootstrap_info(root, CREDENTIALS)
    info._oracle['tarball_path'] = os.path.join(root, 'debian-jessie.tar.gz')
    with open(info._oracle['tarball_path'], 'wb') as tarball:
        tarball.write(tarball_content)
    api.Connect.run(info)
    return info


def test_wrong_credentials_fail_before_the_build(tmp_path, storage):
    info = bootstrap_info(tmp_path, dict(CREDENTIALS, password='wrong'))
    with pytest.raises(RuntimeError, match='Unauthorized'):
        api.Connect.run(info)
    assert not storage.objects


def test_image_tarball(tmp_path, monkeypatch):
    commands = []

    def log_check_call(command):
        commands.append(command)
        return []
    monkeypatch.setattr(image, 'log_check_call', log_check_call)
    info = bootstrap_info(tmp_path)
    image.CreateImageTarball.run(info)
    tarball = os.path.join(tmp_path, 'debian-jessie.tar.gz')
    assert commands == [['tar', '--sparse', '-C', str(tmp_path), '-caf', tarball, 'debian-jessie.raw']]
    assert info._oracle['tarball_path'] == tarball


def test_large_tarball_is_uploaded_in_segments(tmp_path, storage, monkeypatch):
    # Segments of 16 bytes instead of 50 MiB keep the test small. With more than 9 segments,
    # the storage only joins them in upload order when their numbers are zero padded.
    monkeypatch.setattr(apiclient.OracleStorageAPIClient, 'chunk_size', 16)
    info = connect(tmp_path, bytes(range(200)))
    image.UploadImageTarball.run(info)
    segments = sorted(name for name in storage.objects if name.startswith(CONTAINER + 'debian-jessie.tar.gz-'))
    assert len(segments) == 13
    manifest_content, manifest_headers = storage.objects[CONTAINER + 'debian-jessie.tar.gz']
    assert manifest_headers['X-Object-Manifest'] == 'images/debian-jessie.tar.gz-'
    assert not manifest_content
    uploaded = storage.download(CONTAINER + 'debian-jessie.tar.gz')
    with open(info._oracle['tarball_path'], 'rb') as tarball:
        assert uploaded == tarball.read()


def test_verification_downloads_the_upload_and_removes_the_copy(tmp_path, storage, caplog):
    info = connect(tmp_path, b'image tarball')
    image.UploadImageTarball.run(info)
    image.DownloadImageTarball.run(info)
    [downloaded] = set(os.listdir(tmp_path)) - {'debian-jessie.tar.gz'}
    with open(os.path.join(tmp_path, downloaded), 'rb') as copy:
        assert copy.read() == b'image tarball'
    image.CompareImageTarballs.run(info)
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert sorted(os.listdir(tmp_path)) == ['debian-jessie.tar.gz']


def test_default_packages(tmp_path):
    info = bootstrap_info(tmp_path)
    packages.DefaultPackages.run(info)
    network.InstallDHCPCD.run(info)
    assert sorted(package.name for package in info.packages.remote()) == ['dhcpcd5', 'linux-image-amd64']
    assert {'isc-dhcp-client', 'isc-dhcp-common'} <= info.exclude_packages
