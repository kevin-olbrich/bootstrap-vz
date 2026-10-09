import os.path
import subprocess
import pytest
from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.common import tools
from bootstrapvz.common.exceptions import ManifestError
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.pip3_install import tasks as pip3_tasks
from bootstrapvz.plugins.pip_install import tasks as pip_tasks

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/buster-cloudimg.yml')

PLUGINS = ['pip_install', 'pip3_install']
PACKAGES = ['awscli==1.3.13', 'boto3']


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    """Fails the test instead of running a command that the test did not mock"""
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {args}'.format(args=args or kwargs))
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)


def manifest_data(plugin, settings):
    data = load_data(example)
    data['plugins'][plugin] = settings
    return data


def pip_info(plugin, release, root=None):
    data = manifest_data(plugin, {'packages': list(PACKAGES)})
    data['system']['release'] = release
    return DictClass(manifest=Manifest(path=example, data=data), packages=set(), root=root)


def record_commands(monkeypatch):
    commands = []

    def log_check_call(command):
        commands.append(command)
        return []
    monkeypatch.setattr(tools, 'log_check_call', log_check_call)
    return commands


@pytest.mark.parametrize('plugin', PLUGINS)
@pytest.mark.parametrize('settings', [{},
                                      {'package': ['awscli']},
                                      {'packages': 'awscli'},
                                      {'packages': []}],
                         ids=['missing packages', 'misspelled key', 'string', 'empty list'])
def test_invalid_settings_rejected(plugin, settings):
    with pytest.raises(ManifestError) as excinfo:
        Manifest(path=example, data=manifest_data(plugin, settings))
    assert list(excinfo.value.data_path)[:2] == ['plugins', plugin]


@pytest.mark.parametrize('plugin', PLUGINS)
def test_valid_settings_accepted(plugin):
    Manifest(path=example, data=manifest_data(plugin, {'packages': ['awscli==1.3.13', 'boto3']}))


@pytest.mark.parametrize('release, packages', [
    ('stretch', {'python-pip', 'build-essential', 'python-dev'}),
    ('buster', {'python-pip', 'build-essential', 'python-dev'}),
    # Debian dropped pip and the headers for Python 2 in bullseye
    ('bullseye', {'python3-pip', 'build-essential', 'python3-dev'}),
    ('stable', {'python3-pip', 'build-essential', 'python3-dev'}),
])
def test_pip_install_adds_pip_and_build_tools(release, packages):
    info = pip_info('pip_install', release)
    pip_tasks.AddPipPackage.run(info)
    assert info.packages == packages


@pytest.mark.parametrize('release', ['stretch', 'trixie'])
def test_pip3_install_adds_pip3_and_build_tools(release):
    info = pip_info('pip3_install', release)
    pip3_tasks.AddPip3Package.run(info)
    assert info.packages == {'python3-pip', 'build-essential', 'python3-dev'}


@pytest.mark.parametrize('release, command', [
    ('buster', ['pip', 'install']),
    ('bullseye', ['pip3', 'install']),
    # The system Python is externally managed (PEP 668) from bookworm on
    ('bookworm', ['pip3', 'install', '--break-system-packages']),
    ('testing', ['pip3', 'install', '--break-system-packages']),
])
def test_pip_install_installs_packages_in_the_image(tmp_path, monkeypatch, release, command):
    commands = record_commands(monkeypatch)
    pip_tasks.PipInstallCommand.run(pip_info('pip_install', release, str(tmp_path)))
    assert commands == [['chroot', str(tmp_path)] + command + PACKAGES]


@pytest.mark.parametrize('release, command', [
    ('stretch', ['pip3', 'install']),
    ('bullseye', ['pip3', 'install']),
    ('bookworm', ['pip3', 'install', '--break-system-packages']),
    ('stable', ['pip3', 'install', '--break-system-packages']),
])
def test_pip3_install_installs_packages_in_the_image(tmp_path, monkeypatch, release, command):
    commands = record_commands(monkeypatch)
    pip3_tasks.Pip3InstallCommand.run(pip_info('pip3_install', release, str(tmp_path)))
    assert commands == [['chroot', str(tmp_path)] + command + PACKAGES]
