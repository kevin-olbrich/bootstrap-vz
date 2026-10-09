import os
import subprocess

import pytest
import yaml
from bootstrapvz.base.bootstrapinfo import BootstrapInformation, DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.common.tasks import apt
from bootstrapvz.common.tools import load_data, load_yaml
from bootstrapvz.plugins.cloud_init import tasks

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/buster-cloudimg.yml')

# The default user in /etc/cloud/cloud.cfg as cloud-init 23.3 and newer renders it for Debian
CLOUD_CFG_23_3 = '''\
users:
  - default

system_info:
  # This will affect which distro class gets used
  distro: debian
  # Default user name + that default users groups (if added/used)
  default_user:
    name: debian
    lock_passwd: True
    gecos: Debian
    groups: [adm, audio, cdrom, dialout, dip, floppy, netdev, plugdev, sudo, video]
    sudo: ["ALL=(ALL) NOPASSWD:ALL"]
    shell: /bin/bash
'''

# The layout that the cloud.cfg regular expressions were written for (wheezy and jessie)
CLOUD_CFG_0_7 = '''\
users:
 - default

system_info:
   distro: debian
   default_user:
     name: debian
     lock_passwd: True
     gecos: Debian
     groups: [adm, audio, cdrom, dialout, floppy, video, plugdev, dip]
     sudo: ["ALL=(ALL) NOPASSWD:ALL"]
     shell: /bin/bash
'''

DROP_IN = 'etc/cloud/cloud.cfg.d/02_bootstrapvz_user.cfg'

# The module lists in /etc/cloud/cloud.cfg, cloud-init 23.3 and newer indent them with two spaces
MODULES = '''
cloud_init_modules:
{indent}- migrator
{indent}- growpart
{indent}- resizefs
{indent}- ssh

cloud_config_modules:
{indent}- ssh_import_id
{indent}- locale

cloud_final_modules:
{indent}- ssh_authkey_fingerprints
{indent}- final_message
'''


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


def prepare_root(root, cloud_cfg, options):
    os.makedirs(os.path.join(root, 'etc/cloud/cloud.cfg.d'))
    with open(os.path.join(root, 'etc/cloud/cloud.cfg'), 'w', encoding='utf-8') as handle:
        handle.write(cloud_cfg)
    return DictClass(root=str(root), manifest=DictClass(plugins={'cloud_init': options}))


@pytest.mark.parametrize('cloud_cfg', [CLOUD_CFG_23_3, CLOUD_CFG_0_7], ids=['cloud-init-23.3', 'cloud-init-0.7'])
def test_username_is_set_in_drop_in(tmp_path, cloud_cfg):
    info = prepare_root(tmp_path, cloud_cfg, {'username': 'admin'})
    tasks.SetCloudInitMountOptions.run(info)
    tasks.SetUsername.run(info)
    # cloud-init merges cloud.cfg.d over cloud.cfg, so the other default_user keys keep their packaged values
    assert load_yaml(os.path.join(info.root, DROP_IN)) == {
        'system_info': {'default_user': {'name': 'admin',
                                         'sudo': 'ALL=(ALL) NOPASSWD:ALL',
                                         'shell': '/bin/bash'}}}
    # Later files in cloud.cfg.d win, so the user settings must sort after the plugin defaults
    assert sorted(os.listdir(os.path.join(info.root, 'etc/cloud/cloud.cfg.d'))) == [
        '01_debian_cloud.cfg', '02_bootstrapvz_user.cfg']
    with open(os.path.join(info.root, 'etc/cloud/cloud.cfg'), encoding='utf-8') as handle:
        assert handle.read() == cloud_cfg


@pytest.mark.parametrize('cloud_cfg', [CLOUD_CFG_23_3, CLOUD_CFG_0_7], ids=['cloud-init-23.3', 'cloud-init-0.7'])
def test_groups_are_added_to_packaged_groups(tmp_path, cloud_cfg):
    info = prepare_root(tmp_path, cloud_cfg, {'username': 'admin', 'groups': ['docker', 'kvm']})
    tasks.SetUsername.run(info)
    tasks.SetGroups.run(info)
    # cloud-init replaces a list from cloud.cfg.d instead of merging it, so the packaged groups are kept here
    packaged_groups = load_yaml(os.path.join(info.root, 'etc/cloud/cloud.cfg'))['system_info']['default_user']['groups']
    assert load_yaml(os.path.join(info.root, DROP_IN)) == {
        'system_info': {'default_user': {'name': 'admin',
                                         'groups': packaged_groups + ['docker', 'kvm'],
                                         'sudo': 'ALL=(ALL) NOPASSWD:ALL',
                                         'shell': '/bin/bash'}}}


@pytest.mark.parametrize('cloud_cfg, indent', [(CLOUD_CFG_23_3, '  '), (CLOUD_CFG_0_7, ' ')],
                         ids=['cloud-init-23.3', 'cloud-init-0.7'])
def test_disabled_modules_removed_from_cloud_cfg(tmp_path, cloud_cfg, indent):
    info = prepare_root(tmp_path, cloud_cfg + MODULES.format(indent=indent),
                        {'username': 'admin', 'disable_modules': ['growpart', 'resizefs', 'ssh']})
    tasks.DisableModules.run(info)
    expected = yaml.safe_load(cloud_cfg)
    # Modules whose names only start with a disabled one stay enabled
    expected.update(cloud_init_modules=['migrator'],
                    cloud_config_modules=['ssh_import_id', 'locale'],
                    cloud_final_modules=['ssh_authkey_fingerprints', 'final_message'])
    assert load_yaml(os.path.join(info.root, 'etc/cloud/cloud.cfg')) == expected


def metadata_info(root, provider, options):
    return DictClass(root=str(root), manifest=DictClass(provider={'name': provider},
                                                        plugins={'cloud_init': {'username': 'admin', **options}}))


@pytest.mark.parametrize('provider, options, sources', [('ec2', {}, 'Ec2'),
                                                        ('ec2', {'metadata_sources': 'Ec2, None'}, 'Ec2, None'),
                                                        ('kvm', {'metadata_sources': 'NoCloud'}, 'NoCloud')],
                         ids=['ec2 default', 'ec2', 'kvm'])
def test_metadata_sources_preseeded(tmp_path, monkeypatch, provider, options, sources):
    selections = []

    def log_check_call(command, stdin):
        selections.append((command, stdin))
        return []
    monkeypatch.setattr(tasks, 'log_check_call', log_check_call)
    tasks.SetMetadataSource.run(metadata_info(tmp_path, provider, options))
    assert selections == [(['chroot', str(tmp_path), 'debconf-set-selections'],
                           'cloud-init    cloud-init/datasources    multiselect    ' + sources)]


def test_metadata_sources_left_to_package_without_provider_default(tmp_path, caplog):
    # no_external_commands fails the test if debconf-set-selections runs
    tasks.SetMetadataSource.run(metadata_info(tmp_path, 'kvm', {}))
    assert 'No cloud-init metadata source mapping found for provider `kvm\'' in caplog.text


@pytest.mark.parametrize('release, cloud_init', [('wheezy', 'cloud-init/wheezy-backports'),
                                                 ('jessie', 'cloud-init'),
                                                 ('trixie', 'cloud-init')])
def test_cloud_init_package_from_backports_on_wheezy(tmp_path, release, cloud_init):
    data = load_data(example)
    data['bootstrapper']['workspace'] = str(tmp_path)
    data['system']['release'] = release
    data['plugins'] = {'cloud_init': {'username': 'admin'}}
    info = BootstrapInformation(manifest=Manifest(path=example, data=data))
    # On wheezy the plugin adds the backports sources, which AddCloudInitPackages runs after
    apt.AddBackports.run(info)
    tasks.AddCloudInitPackages.run(info)
    assert [str(package) for package in info.packages.install] == [cloud_init, 'sudo']
