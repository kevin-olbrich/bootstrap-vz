import os

import pytest
from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.common.tools import load_yaml
from bootstrapvz.plugins.cloud_init import tasks

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
