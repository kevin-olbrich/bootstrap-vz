import io
import os.path
import subprocess
import urllib.request
import pytest
import yaml
from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common import tools
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.salt import tasks

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/trixie-openvox.yaml')

# The part of bootstrap-salt.sh that the plugin changes
SCRIPT = ('#!/bin/sh -\n'
          'install_debian_check_services() {\n'
          '    __check_services_systemd salt-minion || return 1\n'
          '}\n')


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    """Fails the test instead of running a command or a download that the test did not mock"""
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {args}'.format(args=args or kwargs))
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)
    monkeypatch.setattr(urllib.request, 'urlopen', refuse)


def salt_manifest(settings):
    data = load_data(example)
    data['plugins'] = {'salt': settings}
    return Manifest(path=example, data=data)


@pytest.mark.parametrize('settings', [{'install_source': 'stable'},
                                      {'install_source': 'stable', 'grains': {'role': 'web'}}],
                         ids=['without grains', 'with grains'])
def test_resolved_tasks(settings):
    resolved = load_tasks('resolve_tasks', salt_manifest(settings))
    assert {tasks.InstallSaltDependencies, tasks.BootstrapSaltMinion} <= resolved
    assert (tasks.SetSaltGrains in resolved) == ('grains' in settings)


def test_bootstrap_script_dependencies_are_installed():
    info = DictClass(packages=set())
    tasks.InstallSaltDependencies.run(info)
    assert info.packages == {'curl', 'ca-certificates'}


@pytest.mark.parametrize('settings, arguments', [
    ({'install_source': 'stable'}, ['stable']),
    ({'install_source': 'git', 'version': 'v3006.9'}, ['git', 'v3006.9']),
    ({'install_source': 'stable', 'master': 'salt.example.org'}, ['-A', 'salt.example.org', 'stable']),
], ids=['stable', 'git version', 'master'])
def test_minion_is_installed_with_the_bootstrap_script(tmp_path, monkeypatch, settings, arguments):
    urls = []

    def urlopen(url, timeout=None):
        urls.append(url)
        return io.BytesIO(SCRIPT.encode('utf-8'))
    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    runs = []

    def log_check_call(command):
        # The script that bash finds in the image
        runs.append((command, (tmp_path / 'install_salt.sh').read_text(encoding='utf-8')))
        return []
    monkeypatch.setattr(tasks, 'log_check_call', log_check_call)

    tasks.BootstrapSaltMinion.run(DictClass(root=str(tmp_path), manifest=salt_manifest(settings)))
    assert len(urls) == 1
    assert urls[0].startswith('https://')
    # -X keeps the script from starting the minion in the chroot.
    # Nothing runs in the chroot, so the check for running services is disabled.
    assert runs == [(['chroot', str(tmp_path), 'bash', 'install_salt.sh', '-X'] + arguments,
                     SCRIPT.replace('install_debian_check_services', 'disabled_debian_check_services'))]


def test_grains_are_written_to_the_minion_config(tmp_path):
    (tmp_path / 'etc/salt').mkdir(parents=True)
    grains = {'role': 'web', 'datacenter': 'fra1'}
    info = DictClass(root=str(tmp_path), manifest=salt_manifest({'install_source': 'stable', 'grains': grains}))
    tasks.SetSaltGrains.run(info)
    with open(tmp_path / 'etc/salt/grains', encoding='utf-8') as grains_file:
        assert yaml.safe_load(grains_file) == grains
