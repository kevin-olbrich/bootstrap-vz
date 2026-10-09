import os.path
import subprocess

import pytest

import bootstrapvz.plugins
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common import tools
from bootstrapvz.common.exceptions import ManifestError
from bootstrapvz.common.tasks import (apt, bootstrap, dpkg, filesystem, folder, image, initd, locale,
                                      loopback, partitioning, ssh, volume, workspace)
from bootstrapvz.plugins import debconf
from bootstrapvz.plugins.admin_user import tasks as admin_user
from bootstrapvz.plugins.ansible import tasks as ansible
from bootstrapvz.plugins.apt_proxy import tasks as apt_proxy
from bootstrapvz.plugins.chef import tasks as chef
from bootstrapvz.plugins.cloud_init import tasks as cloud_init
from bootstrapvz.plugins.commands import tasks as commands
from bootstrapvz.plugins.debconf.tasks import DebconfSetSelections
from bootstrapvz.plugins.docker_daemon import tasks as docker_daemon
from bootstrapvz.plugins.ec2_launch import tasks as ec2_launch
from bootstrapvz.plugins.ec2_publish import tasks as ec2_publish
from bootstrapvz.plugins.expand_root import tasks as expand_root
from bootstrapvz.plugins.file_copy import tasks as file_copy
from bootstrapvz.plugins.google_cloud_repo import tasks as google_cloud_repo
from bootstrapvz.plugins.minimize_size.tasks import apt as minimize_apt, dpkg as minimize_dpkg, mounts, shrink
from bootstrapvz.plugins.ntp import tasks as ntp
from bootstrapvz.plugins.opennebula import tasks as opennebula
from bootstrapvz.plugins.openvox import tasks as openvox
from bootstrapvz.plugins.pip3_install import tasks as pip3_install
from bootstrapvz.plugins.pip_install import tasks as pip_install
from bootstrapvz.plugins.prebootstrapped import tasks as prebootstrapped
from bootstrapvz.plugins.root_password import tasks as root_password
from bootstrapvz.plugins.salt import tasks as salt
from bootstrapvz.plugins.tmpfs_workspace import tasks as tmpfs_workspace
from bootstrapvz.plugins.unattended_upgrades import tasks as unattended_upgrades
from bootstrapvz.plugins.vagrant import tasks as vagrant
from bootstrapvz.providers.ec2.tasks import ebs, initd as ec2_initd

manifests = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests')
plugins = os.path.dirname(bootstrapvz.plugins.__file__)
PLUGINS = sorted(name for name in os.listdir(plugins)
                 if os.path.isfile(os.path.join(plugins, name, '__init__.py')))
# pip_install_tests.py rejects the invalid settings of these plugins
INVALID_SETTINGS_TESTED_ELSEWHERE = ['pip3_install', 'pip_install']
# jsonschema warns that the $schema URI of the debconf schema is unknown whenever it validates
# a manifest against that schema, so test_debconf calls the plugin functions without the schema
TESTED_WITHOUT_SCHEMA = ['debconf']


def base(path, **system):
    """A manifest in manifests/ and the system settings to change in it"""
    return os.path.join(manifests, path), system


KVM = base('examples/kvm/trixie-openvox.yaml')
KVM_QCOW2 = base('examples/kvm/buster-cloudimg.yml', hostname='box')
KVM_STRETCH = base('examples/kvm/stretch-cloudimg.yml')
KVM_JESSIE = base('examples/kvm/jessie-virtio.yml')
KVM_WHEEZY = base('examples/kvm/wheezy.yml')
EC2 = base('examples/ec2/ebs-unstable-amd64-pvm.yml')
EC2_PARTITIONED = base('official/ec2/ebs-stretch-amd64-hvm.yml')
GCE = base('official/gce/buster.yml')
VIRTUALBOX = base('examples/virtualbox/stretch-vagrant.yml')
DOCKER = base('examples/docker/stretch.yml')

# The selections of the debconf README, one per line as debconf-set-selections reads them
SELECTIONS = ('d-i pkgsel/install-language-support boolean false\n'
              'popularity-contest popularity-contest/participate boolean false')
EC2_LAUNCH = {'security_group_ids': ['sg-0123456789abcdef0'], 'ssh_key': 'build'}
EXPAND_ROOT = {'filesystem_type': 'ext4', 'root_device': '/dev/xvda', 'root_partition': 1}
INTERVALS = {'update_interval': 1, 'download_interval': 1, 'upgrade_interval': 1}

ADMIN_USER_TASKS = {admin_user.AddSudoPackage, admin_user.CreateAdminUser, admin_user.PasswordlessSudo}
DOCKER_TASKS = {docker_daemon.AddDockerAptSource, docker_daemon.InstallDockerAptKey,
                docker_daemon.AddDockerPackages, docker_daemon.EnableMemoryCgroup}
OPENVOX_TASKS = {openvox.AddOpenVoxAptSource, openvox.InstallOpenVoxAptKey, openvox.AddOpenVoxAgentPackage}
VAGRANT_TASKS = {vagrant.CheckBoxPath, vagrant.CreateVagrantBoxDir, vagrant.AddPackages,
                 vagrant.CreateVagrantUser, vagrant.PasswordlessSudo, vagrant.SetRootPassword,
                 vagrant.AddInsecurePublicKey, vagrant.PackageBox, vagrant.RemoveVagrantBoxDir,
                 volume.Delete, ssh.DisableSSHDNSLookup}
# What restoring a prebootstrapped volume replaces in every base manifest
PREBOOTSTRAPPED_SKIPPED = {apt.DisableDaemonAutostart, bootstrap.Bootstrap, locale.GenerateLocale}

# The minimal settings from the README of each plugin on a base manifest the plugin supports,
# and the tasks the plugin adds to and removes from the tasks of the base manifest
MINIMAL = {
    'admin_user': (KVM, {'username': 'admin'}, ADMIN_USER_TASKS, set()),
    # The plugin has no README, playbook is the only required setting
    'ansible': (KVM, {'playbook': 'site.yml'},
                {ansible.AddRequiredCommands, ansible.CheckPlaybookPath, ansible.AddPackages,
                 ansible.RunAnsiblePlaybook}, set()),
    'apt_proxy': (KVM, {'address': '127.0.0.1', 'port': 3142},
                  {apt_proxy.CheckAptProxy, apt_proxy.SetBootstrapProxy, apt_proxy.SetAptProxy,
                   apt_proxy.RemoveAptProxy}, set()),
    # The plugin has no README, and Debian dropped the chef package after stretch (see TODO.md)
    'chef': (KVM_STRETCH, {'assets': '/srv/chef'},
             {chef.CheckAssetsPath, chef.AddPackages, chef.CopyChefAssets}, set()),
    'cloud_init': (EC2_PARTITIONED, {'username': 'admin'},
                   {cloud_init.AddCloudInitPackages, cloud_init.SetMetadataSource, cloud_init.SetUsername,
                    cloud_init.SetCloudInitMountOptions},
                   # cloud-init takes over what these init scripts do
                   {ec2_initd.AddEC2InitScripts, initd.AddExpandRoot, initd.AdjustExpandRootScript,
                    initd.AdjustGrowpartWorkaround, ssh.AddSSHKeyGeneration}),
    'commands': (KVM, {'commands': [['touch', '{root}/var/www/index.html']]},
                 {commands.ImageExecuteCommand}, set()),
    'docker_daemon': (KVM, {}, DOCKER_TASKS, set()),
    'ec2_launch': (EC2, EC2_LAUNCH, {ec2_launch.LaunchEC2Instance}, set()),
    'ec2_publish': (EC2, {'regions': ['eu-west-1']}, {ec2_publish.CopyAmiToRegions}, set()),
    'expand_root': (EC2_PARTITIONED, EXPAND_ROOT,
                    {expand_root.InstallGrowpart, expand_root.InstallExpandRootScripts},
                    # The plugin replaces the common expand-root script
                    {initd.AddExpandRoot, initd.AdjustExpandRootScript, initd.AdjustGrowpartWorkaround}),
    'file_copy': (KVM, {'files': [{'src': 'motd', 'dst': '/etc/motd'}]},
                  {file_copy.ValidateFiles, file_copy.FileCopyCommand}, set()),
    'google_cloud_repo': (GCE, {}, {google_cloud_repo.AddGoogleCloudRepoKey}, set()),
    'minimize_size': (KVM, {}, {mounts.AddFolderMounts, mounts.RemoveFolderMounts}, set()),
    'ntp': (KVM, {}, {ntp.AddNtpPackage}, set()),
    # Debian shipped opennebula-context only until the jessie era (see TODO.md)
    'opennebula': (KVM_JESSIE, {}, {opennebula.AddONEContextPackage}, set()),
    'openvox': (KVM, {'collection': 'openvox8'}, OPENVOX_TASKS, set()),
    'pip3_install': (KVM, {'packages': ['awscli']},
                     {pip3_install.AddPip3Package, pip3_install.Pip3InstallCommand}, set()),
    'pip_install': (KVM, {'packages': ['awscli']},
                    {pip_install.AddPipPackage, pip_install.PipInstallCommand}, set()),
    'prebootstrapped': (EC2, {}, {prebootstrapped.Snapshot}, set()),
    'root_password': (EC2, {'password': 's3cr3t'}, {ssh.EnableRootLogin, root_password.SetRootPassword},
                      {ssh.DisableSSHPasswordAuthentication}),
    # The current salt-bootstrap script refuses releases before bookworm (see TODO.md)
    'salt': (KVM, {'install_source': 'stable'},
             {salt.InstallSaltDependencies, salt.BootstrapSaltMinion}, set()),
    'tmpfs_workspace': (KVM, {},
                        {tmpfs_workspace.CreateTmpFsWorkspace, tmpfs_workspace.MountTmpFsWorkspace,
                         tmpfs_workspace.UnmountTmpFsWorkspace, tmpfs_workspace.DeleteTmpFsWorkspace},
                        {workspace.CreateWorkspace, workspace.DeleteWorkspace}),
    'unattended_upgrades': (KVM, INTERVALS,
                            {unattended_upgrades.AddUnattendedUpgradesPackage,
                             unattended_upgrades.EnablePeriodicUpgrades}, set()),
    'vagrant': (VIRTUALBOX, {},
                VAGRANT_TASKS | {ssh.AddOpenSSHPackage, ssh.AddSSHKeyGeneration, ssh.ShredHostkeys,
                                 initd.InstallInitScripts},
                {image.MoveImage}),
}

# Settings that the README of a plugin describes as adding or removing tasks
SETTINGS = [
    pytest.param('admin_user', EC2, {'username': 'admin'},
                 ADMIN_USER_TASKS | {admin_user.AdminUserPublicKeyEC2}, set(), id='admin_user-ec2-key'),
    pytest.param('admin_user', EC2,
                 {'username': 'admin', 'password': 's3cr3t', 'pubkey': '/home/admin/.ssh/id.pub'},
                 ADMIN_USER_TASKS | {admin_user.AdminUserPassword, admin_user.CheckPublicKeyFile,
                                     admin_user.AdminUserPublicKey},
                 {ssh.DisableSSHPasswordAuthentication}, id='admin_user-password-pubkey'),
    # From jessie on, sshd does not allow root to log in with a password by default
    pytest.param('admin_user', KVM_WHEEZY, {'username': 'admin'}, ADMIN_USER_TASKS | {ssh.DisableRootLogin},
                 set(), id='admin_user-wheezy-root-login'),
    pytest.param('ansible', KVM, {'playbook': 'site.yml', 'extra_vars': {'ansible_ssh_user': 'admin'}},
                 {ansible.AddRequiredCommands, ansible.CheckPlaybookPath, ansible.AddPackages,
                  ansible.RunAnsiblePlaybook, ansible.RemoveAnsibleSSHUserDir}, set(), id='ansible-ssh-user'),
    pytest.param('apt_proxy', KVM, {'address': '127.0.0.1', 'port': 3142, 'persistent': True},
                 {apt_proxy.CheckAptProxy, apt_proxy.SetBootstrapProxy, apt_proxy.SetAptProxy}, set(),
                 id='apt_proxy-persistent'),
    pytest.param('cloud_init', KVM_WHEEZY,
                 {'username': 'admin', 'groups': ['docker'], 'disable_modules': ['ntp']},
                 {apt.AddBackports, cloud_init.AddCloudInitPackages, cloud_init.SetMetadataSource,
                  cloud_init.SetUsername, cloud_init.SetGroups, cloud_init.DisableModules},
                 {ssh.AddSSHKeyGeneration}, id='cloud_init-wheezy-backports'),
    pytest.param('docker_daemon', KVM, {'version': '29.0.1', 'docker_opts': '--dns 8.8.8.8'},
                 DOCKER_TASKS | {docker_daemon.PinDockerVersion, apt.WritePreferences,
                                 docker_daemon.SetDockerOpts},
                 set(), id='docker_daemon-version-opts'),
    pytest.param('ec2_launch', EC2, dict(EC2_LAUNCH, print_public_ip='/tmp/ip', deregister_ami=True),
                 {ec2_launch.LaunchEC2Instance, ec2_launch.PrintPublicIPAddress, ec2_launch.DeregisterAMI},
                 set(), id='ec2_launch-print-ip-deregister'),
    pytest.param('ec2_publish', EC2, {'regions': ['eu-west-1'], 'public': True},
                 {ec2_publish.CopyAmiToRegions, ec2_publish.PublishAmi}, set(), id='ec2_publish-public'),
    pytest.param('file_copy', KVM,
                 {'mkdirs': [{'dir': '/srv'}], 'files': [{'src': 'motd', 'dst': '/etc/motd'}]},
                 {file_copy.MkdirCommand, file_copy.ValidateFiles, file_copy.FileCopyCommand}, set(),
                 id='file_copy-mkdirs'),
    pytest.param('google_cloud_repo', GCE, {'enable_keyring_repo': True, 'cleanup_bootstrap_key': True},
                 {google_cloud_repo.AddGoogleCloudRepoKey, google_cloud_repo.AddGoogleCloudRepoKeyringRepo,
                  google_cloud_repo.InstallGoogleCloudRepoKeyringPackage,
                  google_cloud_repo.CleanupBootstrapRepoKey},
                 set(), id='google_cloud_repo-keyring'),
    pytest.param('minimize_size', KVM,
                 {'zerofree': True, 'shrink': 'qemu-img',
                  'apt': {'autoclean': True, 'languages': ['none'], 'gzip_indexes': True,
                          'autoremove_suggests': True},
                  'dpkg': {'exclude_docs': True}},
                 {mounts.AddFolderMounts, mounts.RemoveFolderMounts,
                  shrink.AddRequiredZeroFreeCommand, shrink.Zerofree,
                  shrink.AddRequiredQemuImgCommand, shrink.ShrinkVolumeWithQemuImg,
                  minimize_apt.AutomateAptClean, minimize_apt.FilterTranslationFiles,
                  minimize_apt.AptGzipIndexes, minimize_apt.AptAutoremoveSuggests,
                  dpkg.CreateDpkgCfg, minimize_dpkg.InitializeBootstrapFilterList,
                  minimize_dpkg.CreateBootstrapFilterScripts, minimize_dpkg.ExcludeDocs,
                  minimize_dpkg.DeleteBootstrapFilterScripts},
                 set(), id='minimize_size-qemu-img'),
    pytest.param('minimize_size', KVM, {'dpkg': {'locales': ['en_US']}},
                 {mounts.AddFolderMounts, mounts.RemoveFolderMounts,
                  dpkg.CreateDpkgCfg, minimize_dpkg.InitializeBootstrapFilterList,
                  minimize_dpkg.CreateBootstrapFilterScripts, minimize_dpkg.FilterLocales,
                  minimize_dpkg.DeleteBootstrapFilterScripts},
                 set(), id='minimize_size-locales'),
    pytest.param('minimize_size', VIRTUALBOX, {'shrink': True},
                 {mounts.AddFolderMounts, mounts.RemoveFolderMounts,
                  shrink.AddRequiredVDiskManagerCommand, shrink.ShrinkVolumeWithVDiskManager},
                 set(), id='minimize_size-vmware-vdiskmanager'),
    pytest.param('ntp', KVM, {'servers': ['time.example.org']}, {ntp.AddNtpPackage, ntp.SetNtpServers}, set(),
                 id='ntp-servers'),
    pytest.param('opennebula', KVM_WHEEZY, {}, {apt.AddBackports, opennebula.AddONEContextPackage}, set(),
                 id='opennebula-wheezy-backports'),
    pytest.param('openvox', KVM,
                 {'assets': '/srv/puppetlabs', 'manifest': '/srv/site.pp',
                  'install_modules': [['puppetlabs-apt']], 'enable_agent': True},
                 OPENVOX_TASKS | {openvox.CopyAssets, openvox.ApplyManifest, openvox.InstallModules,
                                  openvox.EnableAgent},
                 set(), id='openvox-all'),
    pytest.param('prebootstrapped', EC2, {'snapshot': 'snap-0123456789abcdef0'},
                 {prebootstrapped.CreateFromSnapshot},
                 PREBOOTSTRAPPED_SKIPPED | {ebs.Create, filesystem.Format, filesystem.TuneVolumeFS},
                 id='prebootstrapped-from-snapshot'),
    pytest.param('prebootstrapped', KVM, {}, {prebootstrapped.CopyImage}, set(),
                 id='prebootstrapped-copy-image'),
    pytest.param('prebootstrapped', KVM, {'image': '/srv/volume.raw'}, {prebootstrapped.CreateFromImage},
                 PREBOOTSTRAPPED_SKIPPED | {loopback.Create, partitioning.PartitionVolume, filesystem.Format,
                                            filesystem.TuneVolumeFS},
                 id='prebootstrapped-from-image'),
    pytest.param('prebootstrapped', DOCKER, {}, {prebootstrapped.CopyFolder}, set(),
                 id='prebootstrapped-copy-folder'),
    pytest.param('prebootstrapped', DOCKER, {'folder': '/srv/root'}, {prebootstrapped.CreateFromFolder},
                 PREBOOTSTRAPPED_SKIPPED | {folder.Create, dpkg.CreateDpkgCfg},
                 id='prebootstrapped-from-folder'),
    pytest.param('root_password', EC2, {'password-crypted': '$6$salt$hash'},
                 {ssh.EnableRootLogin, root_password.SetRootPassword}, {ssh.DisableSSHPasswordAuthentication},
                 id='root_password-crypted'),
    pytest.param('salt', KVM, {'install_source': 'stable', 'grains': {'role': 'web'}},
                 {salt.InstallSaltDependencies, salt.BootstrapSaltMinion, salt.SetSaltGrains}, set(),
                 id='salt-grains'),
    pytest.param('vagrant', KVM_QCOW2, {'provider': 'libvirt'}, VAGRANT_TASKS, {image.MoveImage},
                 id='vagrant-libvirt'),
]

# Typical mistakes in the settings of each plugin, and the data path and message of the error
INVALID = [
    pytest.param('admin_user', KVM, {}, ['plugins', 'admin_user'], "'username' is a required property",
                 id='admin_user-without-username'),
    pytest.param('admin_user', KVM, {'username': 'admin', 'pubkeys': '/home/admin/.ssh/id.pub'},
                 ['plugins', 'admin_user'],
                 "Additional properties are not allowed ('pubkeys' was unexpected)",
                 id='admin_user-misspelled-key'),
    pytest.param('ansible', KVM, {}, ['plugins', 'ansible'], "'playbook' is a required property",
                 id='ansible-without-playbook'),
    pytest.param('ansible', KVM, {'playbook': 'site.yml', 'tags': 'web'}, ['plugins', 'ansible', 'tags'],
                 "'web' is not of type 'array'", id='ansible-tags-string'),
    pytest.param('apt_proxy', KVM, {'address': '127.0.0.1'}, ['plugins', 'apt_proxy'],
                 "'port' is a required property", id='apt_proxy-without-port'),
    pytest.param('apt_proxy', KVM, {'address': '127.0.0.1', 'port': '3142'}, ['plugins', 'apt_proxy', 'port'],
                 "'3142' is not of type 'integer'", id='apt_proxy-port-string'),
    pytest.param('chef', KVM_STRETCH, {}, ['plugins', 'chef'], "'assets' is a required property",
                 id='chef-without-assets'),
    pytest.param('chef', KVM_STRETCH, {'assets': 'chef'}, ['plugins', 'chef', 'assets'],
                 "'chef' does not match", id='chef-relative-assets'),
    pytest.param('cloud_init', EC2, {'metadata_sources': 'Ec2'}, ['plugins', 'cloud_init'],
                 "'username' is a required property", id='cloud_init-without-username'),
    pytest.param('cloud_init', EC2, {'username': 'admin', 'groups': 'docker'},
                 ['plugins', 'cloud_init', 'groups'], "'docker' is not of type 'array'",
                 id='cloud_init-groups-string'),
    pytest.param('commands', KVM, {}, ['plugins', 'commands'], "'commands' is a required property",
                 id='commands-without-commands'),
    pytest.param('commands', KVM, {'commands': ['touch /srv/index.html']},
                 ['plugins', 'commands', 'commands', 0], "'touch /srv/index.html' is not of type 'array'",
                 id='commands-command-string'),
    pytest.param('docker_daemon', base('examples/kvm/trixie-openvox.yaml', release='stretch'), {},
                 ['system', 'release'], 'Docker does not provide packages for Debian stretch, '
                 'supported releases are: buster, bullseye, bookworm, trixie', id='docker_daemon-stretch'),
    pytest.param('docker_daemon', base('examples/kvm/trixie-openvox.yaml', release='unstable'), {},
                 ['system', 'release'], 'Docker does not provide packages for Debian sid, '
                 'supported releases are: buster, bullseye, bookworm, trixie', id='docker_daemon-unstable'),
    pytest.param('docker_daemon',
                 base('examples/kvm/trixie-openvox.yaml', release='bookworm', architecture='i386'), {},
                 ['system', 'architecture'], "'i386' is not one of ['amd64', 'arm64']",
                 id='docker_daemon-i386'),
    pytest.param('docker_daemon', KVM, {'version': '29.0'}, ['plugins', 'docker_daemon', 'version'],
                 "'29.0' does not match", id='docker_daemon-short-version'),
    pytest.param('ec2_launch', EC2, dict(EC2_LAUNCH, security_group_ids='sg-0123456789abcdef0'),
                 ['plugins', 'ec2_launch', 'security_group_ids'],
                 "'sg-0123456789abcdef0' is not of type 'array'", id='ec2_launch-security-group-string'),
    pytest.param('ec2_launch', EC2, dict(EC2_LAUNCH, deregister_ami='yes'),
                 ['plugins', 'ec2_launch', 'deregister_ami'], "'yes' is not of type 'boolean'",
                 id='ec2_launch-deregister-string'),
    pytest.param('ec2_publish', EC2, {'regions': 'eu-west-1'}, ['plugins', 'ec2_publish', 'regions'],
                 "'eu-west-1' is not of type 'array'", id='ec2_publish-regions-string'),
    pytest.param('ec2_publish', EC2, {'regions': ['eu-west1']}, ['plugins', 'ec2_publish', 'regions', 0],
                 "'eu-west1' is not one of", id='ec2_publish-misspelled-region'),
    pytest.param('expand_root', EC2_PARTITIONED, {'filesystem_type': 'ext4', 'root_device': '/dev/xvda'},
                 ['plugins', 'expand_root'], "'root_partition' is a required property",
                 id='expand_root-without-partition'),
    pytest.param('expand_root', EC2_PARTITIONED, dict(EXPAND_ROOT, filesystem_type='btrfs'),
                 ['plugins', 'expand_root', 'filesystem_type'],
                 "'btrfs' is not one of ['ext2', 'ext3', 'ext4', 'xfs']", id='expand_root-btrfs'),
    pytest.param('file_copy', KVM, {'mkdirs': [{'dir': '/srv'}]}, ['plugins', 'file_copy'],
                 "'files' is a required property", id='file_copy-without-files'),
    pytest.param('file_copy', KVM, {'files': [{'src': 'motd'}]}, ['plugins', 'file_copy', 'files', 0],
                 "'dst' is a required property", id='file_copy-without-dst'),
    # An unquoted 0644 in YAML is the number 420
    pytest.param('file_copy', KVM, {'files': [{'src': 'motd', 'dst': '/etc/motd', 'permissions': 0o644}]},
                 ['plugins', 'file_copy', 'files', 0, 'permissions'], "420 is not of type 'string'",
                 id='file_copy-permissions-number'),
    pytest.param('google_cloud_repo', GCE, {'enable_keyring_repo': 'yes'},
                 ['plugins', 'google_cloud_repo', 'enable_keyring_repo'], "'yes' is not of type 'boolean'",
                 id='google_cloud_repo-keyring-string'),
    pytest.param('minimize_size', KVM, {'shrink': 'vmware-vdiskmanager'},
                 ['plugins', 'minimize_size', 'shrink'],
                 'Can only shrink vmdk images with vmware-vdiskmanager', id='minimize_size-vmware-raw'),
    pytest.param('minimize_size', EC2, {'shrink': 'qemu-img'}, ['plugins', 'minimize_size', 'shrink'],
                 'Can only shrink vmdk, vdi, raw and qcow2 images with qemu-img',
                 id='minimize_size-qemu-img-ebs'),
    pytest.param('minimize_size', KVM, {'zerofree': 'yes'}, ['plugins', 'minimize_size', 'zerofree'],
                 "'yes' is not of type 'boolean'", id='minimize_size-zerofree-string'),
    pytest.param('ntp', KVM, {'servers': 'time.example.org'}, ['plugins', 'ntp', 'servers'],
                 "'time.example.org' is not of type 'array'", id='ntp-servers-string'),
    pytest.param('ntp', KVM, {'servers': []}, ['plugins', 'ntp', 'servers'], '[] should be non-empty',
                 id='ntp-no-servers'),
    pytest.param('openvox', base('examples/kvm/trixie-openvox.yaml', release='buster'),
                 {'collection': 'openvox8'}, ['system', 'release'],
                 'OpenVox is not available for Debian buster, '
                 'supported releases are: bullseye, bookworm, trixie', id='openvox-buster'),
    pytest.param('openvox', KVM, {'manifest': 'site.pp'}, ['plugins', 'openvox', 'manifest'],
                 "'site.pp' does not match", id='openvox-relative-manifest'),
    pytest.param('openvox', KVM, {'install_modules': [['puppetlabs-apt', '9.0.0', 'latest']]},
                 ['plugins', 'openvox', 'install_modules', 0],
                 "['puppetlabs-apt', '9.0.0', 'latest'] is too long", id='openvox-module-too-long'),
    pytest.param('prebootstrapped', EC2, {'snapshot_id': 'snap-0123456789abcdef0'},
                 ['plugins', 'prebootstrapped'],
                 "Additional properties are not allowed ('snapshot_id' was unexpected)",
                 id='prebootstrapped-misspelled-key'),
    pytest.param('root_password', KVM, {}, ['plugins', 'root_password'],
                 '{} is not valid under any of the given schemas', id='root_password-without-password'),
    pytest.param('root_password', KVM, {'password': 's3cr3t', 'password-crypted': '$6$salt$hash'},
                 ['plugins', 'root_password'], 'is valid under each of', id='root_password-both-passwords'),
    pytest.param('salt', KVM, {}, ['plugins', 'salt'], "'install_source' is a required property",
                 id='salt-without-install-source'),
    pytest.param('salt', KVM, {'install_source': 'stabel'}, ['plugins', 'salt', 'install_source'],
                 "'stabel' is not one of", id='salt-misspelled-install-source'),
    pytest.param('salt', KVM, {'install_source': 'stable', 'grains': {'roles': ['web']}},
                 ['plugins', 'salt', 'grains', 'roles'], "['web'] is not of type 'string'",
                 id='salt-grain-list'),
    pytest.param('unattended_upgrades', KVM, {'update_interval': 1, 'download_interval': 1},
                 ['plugins', 'unattended_upgrades'], "'upgrade_interval' is a required property",
                 id='unattended_upgrades-without-upgrade-interval'),
    pytest.param('unattended_upgrades', KVM, dict(INTERVALS, update_interval='1'),
                 ['plugins', 'unattended_upgrades', 'update_interval'], "'1' is not of type 'integer'",
                 id='unattended_upgrades-interval-string'),
    pytest.param('vagrant', VIRTUALBOX, {'provider': 'libvirt'}, ['plugins', 'vagrant', 'provider'],
                 'Libvirt vagrant boxes support qcow2 images only', id='vagrant-libvirt-vmdk'),
    # The box provider defaults to virtualbox
    pytest.param('vagrant', KVM_QCOW2, {}, ['plugins', 'vagrant', 'provider'],
                 'Virtualbox vagrant boxes support vmdk images only', id='vagrant-virtualbox-qcow2'),
    pytest.param('vagrant', base('examples/kvm/buster-cloudimg.yml'), {'provider': 'libvirt'}, ['system'],
                 "'hostname' is a required property", id='vagrant-without-hostname'),
    pytest.param('vagrant', VIRTUALBOX, {'provider': 'vmware'}, ['plugins', 'vagrant', 'provider'],
                 "'vmware' is not one of ['virtualbox', 'libvirt']", id='vagrant-unknown-provider'),
]


@pytest.fixture(autouse=True)
def no_commands(monkeypatch):
    """Fails the test when validating or resolving a plugin runs a command"""
    def run(*args, **kwargs):
        raise AssertionError('Unexpected command: {args} {kwargs}'.format(args=args, kwargs=kwargs))
    monkeypatch.setattr(tools, 'log_call', run)
    monkeypatch.setattr(tools, 'log_check_call', run)
    monkeypatch.setattr(subprocess, 'Popen', run)


def manifest(manifest_base, settings):
    """Loads the base manifest with the given plugin settings in place of its own"""
    path, system = manifest_base
    data = tools.load_data(path)
    data['system'].update(system)
    data['plugins'] = settings
    return Manifest(path=path, data=data)


def changed_tasks(manifest_base, plugin, settings):
    """Returns the tasks that the plugin adds to and removes from the tasks of the base manifest"""
    without = load_tasks('resolve_tasks', manifest(manifest_base, {}))
    with_plugin = load_tasks('resolve_tasks', manifest(manifest_base, {plugin: settings}))
    return with_plugin - without, without - with_plugin


def test_every_plugin_has_minimal_settings():
    assert sorted([*MINIMAL, *TESTED_WITHOUT_SCHEMA]) == PLUGINS


def test_every_plugin_with_a_schema_has_invalid_settings():
    with_schema = [name for name in PLUGINS
                   if os.path.isfile(os.path.join(plugins, name, 'manifest-schema.yml'))]
    tested = ({param.values[0] for param in INVALID} |
              set(INVALID_SETTINGS_TESTED_ELSEWHERE) | set(TESTED_WITHOUT_SCHEMA))
    assert sorted(tested) == with_schema


@pytest.mark.parametrize('plugin', sorted(MINIMAL))
def test_minimal_settings_resolve(plugin):
    manifest_base, settings, added, removed = MINIMAL[plugin]
    assert changed_tasks(manifest_base, plugin, settings) == (added, removed)


@pytest.mark.parametrize('plugin, manifest_base, settings, added, removed', SETTINGS)
def test_settings_resolve(plugin, manifest_base, settings, added, removed):
    assert changed_tasks(manifest_base, plugin, settings) == (added, removed)


def test_debconf(monkeypatch):
    """Checks the selections and resolves the task that sets them, see TESTED_WITHOUT_SCHEMA"""
    commands_run = []

    def recorder(result):
        def run(command, stdin=None, log_stdin=True):
            commands_run.append((command, stdin, log_stdin))
            return result
        return run
    # Answer both command runners, so that the test does not depend on which one checks the selections
    monkeypatch.setattr(tools, 'log_call', recorder((0, [], [])))
    monkeypatch.setattr(tools, 'log_check_call', recorder([]))
    debconf.validate_manifest({'plugins': {'debconf': SELECTIONS}}, lambda data, schema_path: None, None)
    assert commands_run == [(['debconf-set-selections', '--checkonly'], SELECTIONS, False)]
    taskset = set()
    debconf.resolve_tasks(taskset, None)
    assert taskset == {DebconfSetSelections}


@pytest.mark.parametrize('plugin, release', [('docker_daemon', 'buster'), ('docker_daemon', 'stable'),
                                             ('openvox', 'bullseye'), ('openvox', 'oldstable')])
def test_supported_release_accepted(plugin, release):
    (path, system), settings, _, _ = MINIMAL[plugin]
    manifest((path, dict(system, release=release)), {plugin: settings})


@pytest.mark.parametrize('plugin, manifest_base, settings, data_path, message', INVALID)
def test_invalid_settings_rejected(plugin, manifest_base, settings, data_path, message):
    with pytest.raises(ManifestError) as excinfo:
        manifest(manifest_base, {plugin: settings})
    assert list(excinfo.value.data_path) == data_path
    assert message in excinfo.value.message
