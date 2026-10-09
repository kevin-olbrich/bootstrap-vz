import os.path
import subprocess

import pytest

from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.common import releases
from bootstrapvz.common.exceptions import TaskError
from bootstrapvz.plugins.chef import tasks


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


def chef_info(tmp_path, assets):
    root = tmp_path / 'root'
    root.mkdir()
    # stretch still has the Debian chef package, which reads its configuration from /etc/chef
    return DictClass(root=str(root),
                     manifest=DictClass(release=releases.stretch, plugins={'chef': {'assets': str(assets)}}))


def test_assets_copied_to_chef_configuration(tmp_path):
    assets = tmp_path / 'assets'
    (assets / 'trusted_certs').mkdir(parents=True)
    (assets / 'client.rb').write_text("chef_server_url 'https://chef.example.org'\n", encoding='utf-8')
    (assets / 'trusted_certs/chef.example.org.crt').write_text('certificate\n', encoding='utf-8')
    info = chef_info(tmp_path, assets)
    # The configuration directory as the chef package leaves it
    chef_dir = tmp_path / 'root/etc/chef'
    chef_dir.mkdir(parents=True)
    (chef_dir / 'client.rb').write_text('# packaged\n', encoding='utf-8')
    tasks.CopyChefAssets.run(info)
    assert (chef_dir / 'client.rb').read_text(encoding='utf-8') == "chef_server_url 'https://chef.example.org'\n"
    assert (chef_dir / 'trusted_certs/chef.example.org.crt').read_text(encoding='utf-8') == 'certificate\n'


def test_assets_path_checked(tmp_path):
    assets = tmp_path / 'assets'
    info = chef_info(tmp_path, assets)
    with pytest.raises(TaskError, match='does not exist'):
        tasks.CheckAssetsPath.run(info)
    assets.write_text('', encoding='utf-8')
    with pytest.raises(TaskError, match='does not point to a directory'):
        tasks.CheckAssetsPath.run(info)
    os.remove(assets)
    assets.mkdir()
    tasks.CheckAssetsPath.run(info)
