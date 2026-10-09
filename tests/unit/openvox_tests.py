import os.path
import shutil
import stat
import subprocess
import pytest
from bootstrapvz.base.bootstrapinfo import BootstrapInformation, DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common import tools
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.openvox import tasks

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/trixie-openvox.yaml')
key_asset = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                         '../../bootstrapvz/plugins/openvox/assets/openvox.asc')

PUPPET = '/opt/puppetlabs/bin/puppet'


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    """Fails the test instead of running a command that the test did not mock"""
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {args}'.format(args=args or kwargs))
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)


def openvox_manifest(settings, release='trixie'):
    data = load_data(example)
    data['system']['release'] = release
    data['plugins']['openvox'] = settings
    return Manifest(path=example, data=data)


def record_commands(monkeypatch):
    commands = []

    def log_check_call(command):
        commands.append(command)
        return []
    monkeypatch.setattr(tasks, 'log_check_call', log_check_call)
    return commands


@pytest.mark.parametrize('settings, optional', [
    ({'enable_agent': False}, set()),
    ({'assets': '/srv/puppetlabs', 'manifest': '/srv/site.pp',
      'install_modules': [['puppetlabs-stdlib']], 'enable_agent': True},
     {tasks.CopyAssets, tasks.ApplyManifest, tasks.InstallModules, tasks.EnableAgent}),
], ids=['minimal', 'everything'])
def test_resolved_tasks(settings, optional):
    resolved = {task for task in load_tasks('resolve_tasks', openvox_manifest(settings))
                if task.__module__ == tasks.__name__}
    assert resolved == {tasks.AddOpenVoxAptSource, tasks.InstallOpenVoxAptKey, tasks.AddOpenVoxAgentPackage} | optional


@pytest.mark.parametrize('release, settings, suite', [
    ('bullseye', {'enable_agent': False}, 'debian11 openvox8'),
    ('bookworm', {'collection': 'openvox7'}, 'debian12 openvox7'),
    ('trixie', {'enable_agent': False}, 'debian13 openvox8'),
])
def test_agent_is_installed_from_the_signed_repository(tmp_path, monkeypatch, release, settings, suite):
    # The key asset with the mode that a checkout under a restrictive umask gives it
    assets = tmp_path / 'assets'
    assets.mkdir()
    shutil.copy(key_asset, assets)
    os.chmod(assets / 'openvox.asc', 0o600)
    monkeypatch.setattr(tasks, 'ASSETS_DIR', str(assets))
    root = tmp_path / 'root'
    info = BootstrapInformation(openvox_manifest(settings, release))
    info.root = str(root)
    tasks.AddOpenVoxAptSource.run(info)
    tasks.AddOpenVoxAgentPackage.run(info)
    tasks.InstallOpenVoxAptKey.run(info)

    [source] = info.source_lists.sources['openvox']
    assert str(source) == 'deb [signed-by=/etc/apt/keyrings/openvox.asc] https://apt.voxpupuli.org ' + suite
    # apt needs the CA certificates to fetch from the HTTPS repository
    assert 'ca-certificates' in info.include_packages
    assert [str(package) for package in info.packages.install] == ['openvox-agent']
    # The key the source is signed by is installed in the image, readable for the _apt user
    [keyring] = [option[len('signed-by='):] for option in source.options if option.startswith('signed-by=')]
    key = root / keyring.lstrip('/')
    with open(key_asset, 'rb') as asset:
        assert key.read_bytes() == asset.read()
    assert stat.S_IMODE(key.stat().st_mode) == 0o644


def test_modules_are_installed_in_the_image(tmp_path, monkeypatch):
    commands = record_commands(monkeypatch)
    manifest = openvox_manifest({'install_modules': [['puppetlabs-stdlib'],
                                                     ['puppetlabs-apt', '9.4.0'],
                                                     ['puppetlabs/concat', 9]]})
    tasks.InstallModules.run(DictClass(root=str(tmp_path), manifest=manifest))
    install = ['chroot', str(tmp_path), PUPPET, 'module', 'install', '--force']
    assert commands == [install + ['puppetlabs-stdlib'],
                        install + ['puppetlabs-apt', '--version', '9.4.0'],
                        install + ['puppetlabs/concat', '--version', '9']]


def test_assets_are_copied_into_the_configuration_directory(tmp_path):
    assets = tmp_path / 'assets'
    (assets / 'puppet').mkdir(parents=True)
    (assets / 'puppet/puppet.conf').write_text('[main]\nserver = puppet.example.org\n', encoding='utf-8')
    (assets / 'code/environments/production/manifests').mkdir(parents=True)
    (assets / 'code/environments/production/manifests/site.pp').write_text('include base\n', encoding='utf-8')
    # The configuration that the openvox-agent package installed
    config = tmp_path / 'root/etc/puppetlabs'
    (config / 'puppet').mkdir(parents=True)
    (config / 'puppet/puppet.conf').write_text('[main]\n', encoding='utf-8')
    (config / 'puppet/hiera.yaml').write_text('version: 5\n', encoding='utf-8')

    info = DictClass(root=str(tmp_path / 'root'), manifest=openvox_manifest({'assets': str(assets)}))
    tasks.CopyAssets.run(info)
    files = {path.relative_to(config).as_posix(): path.read_text(encoding='utf-8')
             for path in config.rglob('*') if path.is_file()}
    assert files == {'puppet/puppet.conf': '[main]\nserver = puppet.example.org\n',
                     'puppet/hiera.yaml': 'version: 5\n',
                     'code/environments/production/manifests/site.pp': 'include base\n'}


def test_manifest_is_applied_in_the_image(tmp_path, monkeypatch):
    root = tmp_path / 'root'
    (root / 'etc').mkdir(parents=True)
    (root / 'tmp').mkdir()
    (root / 'etc/hostname').write_text('web01\n', encoding='utf-8')
    hosts = '127.0.0.1\tlocalhost\n127.0.1.1\tweb01\n\n::1\t\tlocalhost ip6-localhost ip6-loopback\n'
    (root / 'etc/hosts').write_text(hosts, encoding='utf-8')
    pp_manifest = tmp_path / 'site.pp'
    pp_manifest.write_text("file { '/etc/motd': content => 'managed' }\n", encoding='utf-8')
    runs = []

    def log_check_call(command):
        # What puppet finds in the image while it runs
        runs.append((command,
                     (root / 'etc/hosts').read_text(encoding='utf-8'),
                     (root / 'tmp/site.pp').read_text(encoding='utf-8')))
        return []
    monkeypatch.setattr(tasks, 'log_check_call', log_check_call)

    tasks.ApplyManifest.run(DictClass(root=str(root), manifest=openvox_manifest({'manifest': str(pp_manifest)})))
    assert runs == [(['chroot', str(root), PUPPET, 'apply', '--verbose', '--debug', '/tmp/site.pp'],
                     hosts + '127.0.0.1\tweb01\n',
                     pp_manifest.read_text(encoding='utf-8'))]
    # The temporary hosts entry and the copy of the manifest are removed again
    assert (root / 'etc/hosts').read_text(encoding='utf-8') == hosts
    assert not (root / 'tmp/site.pp').exists()


def test_agent_service_is_enabled(tmp_path, monkeypatch):
    commands = record_commands(monkeypatch)
    tasks.EnableAgent.run(DictClass(root=str(tmp_path)))
    assert commands == [['chroot', str(tmp_path), 'systemctl', 'enable', 'puppet.service']]
