import os.path
import subprocess
import pytest
from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common import tools
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.unattended_upgrades import tasks

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/official/gce/buster.yml')


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    """Fails the test instead of running a command that the test did not mock"""
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {args}'.format(args=args or kwargs))
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)


def manifest(settings):
    data = load_data(example)
    data['plugins']['unattended_upgrades'] = settings
    return Manifest(path=example, data=data)


def test_package_is_installed():
    resolved = load_tasks('resolve_tasks', manifest({'update_interval': 1,
                                                     'download_interval': 1,
                                                     'upgrade_interval': 1}))
    assert {tasks.AddUnattendedUpgradesPackage, tasks.EnablePeriodicUpgrades} <= resolved
    info = DictClass(packages=set())
    tasks.AddUnattendedUpgradesPackage.run(info)
    assert info.packages == {'unattended-upgrades'}


def test_intervals_are_written_to_the_periodic_apt_config(tmp_path):
    (tmp_path / 'etc/apt/apt.conf.d').mkdir(parents=True)
    info = DictClass(root=str(tmp_path), manifest=manifest({'update_interval': 1,
                                                            'download_interval': 2,
                                                            'upgrade_interval': 7}))
    tasks.EnablePeriodicUpgrades.run(info)
    config = (tmp_path / 'etc/apt/apt.conf.d/02periodic').read_text(encoding='utf-8')
    settings = [line for line in config.splitlines() if line and not line.startswith('//')]
    assert settings == ['APT::Periodic::Enable "1";',
                        'APT::Periodic::Update-Package-Lists "1";',
                        'APT::Periodic::Download-Upgradeable-Packages "2";',
                        'APT::Periodic::Unattended-Upgrade "7";']
