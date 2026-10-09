import io
import os
import subprocess
import urllib.error
import urllib.request
from unittest import mock
import pytest
from bootstrapvz.base.bootstrapinfo import BootstrapInformation, DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common import phases
from bootstrapvz.common.tasks import bootstrap
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.apt_proxy import tasks

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/buster-cloudimg.yml')


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


def debootstrap_environments(tmp_path, apt_proxy=None):
    """Returns the environment that debootstrap gets when creating the tarball and when bootstrapping"""
    data = load_data(example)
    data['bootstrapper']['workspace'] = str(tmp_path)
    data['bootstrapper']['tarball'] = True
    if apt_proxy is not None:
        data['plugins']['apt_proxy'] = apt_proxy
    manifest = Manifest(path=example, data=data)
    info = BootstrapInformation(manifest=manifest)
    info.root = os.path.join(info.workspace, 'root')
    # Run the preparation tasks of the plugin, other preparation tasks touch the host
    for task in load_tasks('resolve_tasks', manifest):
        if task.phase is phases.preparation and task.__module__.startswith('bootstrapvz.plugins.apt_proxy'):
            task.run(info)
    with mock.patch('bootstrapvz.common.tools.log_call', return_value=(0, [], [])) as log_call, \
            mock.patch('bootstrapvz.common.tools.log_check_call') as log_check_call:
        bootstrap.MakeTarball.run(info)
        bootstrap.Bootstrap.run(info)
    return [call.call_args.kwargs.get('env') or {} for call in (log_call, log_check_call)]


def test_debootstrap_uses_apt_proxy(tmp_path):
    for env in debootstrap_environments(tmp_path, {'address': '127.0.0.1', 'port': 3142}):
        assert env.get('http_proxy') == 'http://127.0.0.1:3142'
        # debootstrap still needs the environment of the host, e.g. to find its commands
        assert env.get('PATH') == os.environ['PATH']


def test_debootstrap_uses_apt_proxy_credentials(tmp_path):
    apt_proxy = {'address': 'cache.example.org', 'port': 3142, 'username': 'user', 'password': 'secret'}
    for env in debootstrap_environments(tmp_path, apt_proxy):
        assert env.get('http_proxy') == 'http://user:secret@cache.example.org:3142'


def test_debootstrap_without_apt_proxy(tmp_path, monkeypatch):
    monkeypatch.delenv('http_proxy', raising=False)
    for env in debootstrap_environments(tmp_path):
        assert 'http_proxy' not in env


def proxy_info(tmp_path, settings):
    root = tmp_path / 'root'
    (root / 'etc/apt/apt.conf.d').mkdir(parents=True)
    return DictClass(root=str(root), manifest=DictClass(plugins={'apt_proxy': settings}))


@pytest.mark.parametrize('credentials, url', [({'username': 'user', 'password': 'secret'},
                                               'http://user:secret@cache.example.org:3142'),
                                              # The username is ignored without a password
                                              ({'username': 'user'}, 'http://cache.example.org:3142')],
                         ids=['credentials', 'username only'])
def test_apt_uses_proxy_until_cleanup(tmp_path, credentials, url):
    info = proxy_info(tmp_path, {'address': 'cache.example.org', 'port': 3142, **credentials})
    tasks.SetAptProxy.run(info)
    proxy_conf = tmp_path / 'root/etc/apt/apt.conf.d/02proxy'
    assert proxy_conf.read_text(encoding='utf-8') == 'Acquire::http {{ Proxy "{url}"; }};\n'.format(url=url)
    tasks.RemoveAptProxy.run(info)
    assert not proxy_conf.exists()


@pytest.mark.parametrize('settings, persistent', [({}, False),
                                                  ({'persistent': False}, False),
                                                  ({'persistent': True}, True)],
                         ids=['default', 'not persistent', 'persistent'])
def test_persistent_proxy_stays_in_image(settings, persistent):
    data = load_data(example)
    data['plugins']['apt_proxy'] = {'address': '127.0.0.1', 'port': 3142, **settings}
    taskset = load_tasks('resolve_tasks', Manifest(path=example, data=data))
    assert tasks.SetAptProxy in taskset
    assert (tasks.RemoveAptProxy not in taskset) == persistent


@pytest.mark.parametrize('error, warned', [
    (None, False),
    # apt-cacher-ng answers a request for its own address with a usage page
    (urllib.error.HTTPError('http://127.0.0.1:3142', 406, 'Usage Information', {}, None), False),
    (urllib.error.URLError(ConnectionRefusedError(111, 'Connection refused')), True),
], ids=['reachable', 'apt-cacher-ng', 'refused'])
def test_unreachable_proxy_warned(tmp_path, monkeypatch, caplog, error, warned):
    def urlopen(url, timeout):
        if error is not None:
            raise error
        return io.BytesIO(b'')
    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    tasks.CheckAptProxy.run(proxy_info(tmp_path, {'address': '127.0.0.1', 'port': 3142}))
    assert ('The APT proxy server couldn\'t be reached' in caplog.text) == warned
