import os
from bootstrapvz.base import Task
from bootstrapvz.common import phases
from bootstrapvz.common.tasks import apt
from bootstrapvz.common.exceptions import TaskError
from bootstrapvz.common.releases import wheezy, jessie, stretch, bookworm
from bootstrapvz.common.tools import sed_i, log_check_call, rel_path

# Releases that install puppet-agent from the Puppetlabs PC1 repository, with its bundled keyring.
# Newer releases install Puppet from Debian itself, since PC1 was discontinued.
PC1_RELEASES = (wheezy, jessie, stretch)


def uses_pc1(info):
    return info.manifest.release in PC1_RELEASES


def puppet_binary(info):
    return '/opt/puppetlabs/bin/puppet' if uses_pc1(info) else '/usr/bin/puppet'


def puppet_config_dir(info):
    return 'etc/puppetlabs' if uses_pc1(info) else 'etc/puppet'


class CheckRequestedDebianRelease(Task):
    description = 'Checking whether Puppet is available for {info.manifest.release}'
    phase = phases.validation

    @classmethod
    def run(cls, info):
        if info.manifest.release < wheezy:
            msg = 'Puppet is not available for Debian {release}.'.format(release=info.manifest.release)
            raise TaskError(msg)


class CheckAssetsPath(Task):
    description = 'Checking whether the assets path exist'
    phase = phases.validation
    predecessors = [CheckRequestedDebianRelease]

    @classmethod
    def run(cls, info):
        assets = info.manifest.plugins['puppet']['assets']
        if not os.path.exists(assets):
            msg = 'The assets directory {assets} does not exist.'.format(assets=assets)
            raise TaskError(msg)
        if not os.path.isdir(assets):
            msg = 'The assets path {assets} does not point to a directory.'.format(assets=assets)
            raise TaskError(msg)


class CheckManifestPath(Task):
    description = 'Checking whether the manifest file path exist inside the assets'
    phase = phases.validation
    predecessors = [CheckAssetsPath]

    @classmethod
    def run(cls, info):
        manifest = info.manifest.plugins['puppet']['manifest']
        if not os.path.exists(manifest):
            msg = 'The manifest file {manifest} does not exist.'.format(manifest=manifest)
            raise TaskError(msg)
        if not os.path.isfile(manifest):
            msg = 'The manifest path {manifest} does not point to a file.'.format(manifest=manifest)
            raise TaskError(msg)


class InstallPuppetlabsPC1ReleaseKey(Task):
    description = 'Install puppetlabs PC1 Release key into the keyring'
    phase = phases.package_installation
    successors = [apt.WriteSources]

    @classmethod
    def run(cls, info):
        from shutil import copy
        key_path = rel_path(__file__, os.path.join('assets/gpg-keyrings-PC1', info.manifest.release.codename,
                                                   'puppetlabs-pc1-keyring.gpg'))
        destination = os.path.join(info.root, 'etc/apt/trusted.gpg.d/puppetlabs-pc1-keyring.gpg')
        copy(key_path, destination)


class AddPuppetlabsPC1SourcesList(Task):
    description = 'Adding Puppetlabs APT repo to the list of sources.'
    phase = phases.preparation

    @classmethod
    def run(cls, info):
        info.source_lists.add('puppetlabs', 'deb http://apt.puppetlabs.com {codename} PC1'
                              .format(codename=info.manifest.release.codename))


class AddPuppetPackage(Task):
    description = 'Adding the Puppet agent package'
    phase = phases.preparation

    @classmethod
    def run(cls, info):
        if uses_pc1(info):
            # puppet-agent from the PC1 repository
            info.packages.add('puppet-agent')
        elif info.manifest.release < bookworm:
            info.packages.add('puppet')
        else:
            # Debian renamed the agent package in bookworm
            info.packages.add('puppet-agent')


class InstallModules(Task):
    description = 'Installing Puppet modules'
    phase = phases.system_modification

    @classmethod
    def run(cls, info):
        for module in info.manifest.plugins['puppet']['install_modules']:
            command = ['chroot', info.root, puppet_binary(info), 'module', 'install', '--force', str(module[0])]
            if len(module) == 2:
                command.extend(['--version', str(module[1])])
            log_check_call(command)


class CopyPuppetAssets(Task):
    description = 'Copying declared custom puppet assets.'
    phase = phases.system_modification
    predecessors = [InstallModules]

    @classmethod
    def run(cls, info):
        from bootstrapvz.common.tools import copy_tree
        copy_tree(info.manifest.plugins['puppet']['assets'], os.path.join(info.root, puppet_config_dir(info)))


class ApplyPuppetManifest(Task):
    description = 'Applying puppet manifest.'
    phase = phases.system_modification
    predecessors = [CopyPuppetAssets]

    @classmethod
    def run(cls, info):
        with open(os.path.join(info.root, 'etc/hostname'), encoding='utf-8') as handle:
            hostname = handle.read().strip()
        with open(os.path.join(info.root, 'etc/hosts'), 'a', encoding='utf-8') as handle:
            handle.write('127.0.0.1\t{hostname}\n'.format(hostname=hostname))
        from shutil import copy
        pp_manifest = info.manifest.plugins['puppet']['manifest']
        manifest_rel_dst = os.path.join('tmp', os.path.basename(pp_manifest))
        manifest_dst = os.path.join(info.root, manifest_rel_dst)
        copy(pp_manifest, manifest_dst)
        manifest_path = os.path.join('/', manifest_rel_dst)
        log_check_call(['chroot', info.root, puppet_binary(info), 'apply', '--verbose', '--debug', manifest_path])
        os.remove(manifest_dst)
        hosts_path = os.path.join(info.root, 'etc/hosts')
        sed_i(hosts_path, r'127.0.0.1\s*{hostname}\n?'.format(hostname=hostname), '')


class EnableAgent(Task):
    description = 'Enabling the puppet agent'
    phase = phases.system_modification

    @classmethod
    def run(cls, info):
        if info.manifest.release == wheezy:
            # wheezy boots with sysvinit
            log_check_call(['chroot', info.root, 'update-rc.d', 'puppet', 'enable'])
        else:
            log_check_call(['chroot', info.root, 'systemctl', 'enable', 'puppet.service'])
