import os
from unittest import mock
from bootstrapvz.base.bootstrapinfo import BootstrapInformation
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common import phases
from bootstrapvz.common.tasks import bootstrap
from bootstrapvz.common.tools import load_data

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/buster-cloudimg.yml')


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
