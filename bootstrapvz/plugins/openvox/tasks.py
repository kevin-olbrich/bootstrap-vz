import os
from bootstrapvz.base import Task
from bootstrapvz.common import phases
from bootstrapvz.common.tasks import apt
from bootstrapvz.common.tools import sed_i, log_check_call, rel_path

ASSETS_DIR = rel_path(__file__, 'assets')
# Location of the Vox Pupuli APT signing key inside the image
KEYRING_PATH = '/etc/apt/keyrings/openvox.asc'
# OpenVox keeps the Puppet command names and file locations
PUPPET_BINARY = '/opt/puppetlabs/bin/puppet'
CONFIG_DIR = 'etc/puppetlabs'


class AddOpenVoxAptSource(Task):
    description = 'Adding the OpenVox APT repository'
    phase = phases.preparation
    predecessors = [apt.AddManifestSources]

    @classmethod
    def run(cls, info):
        # The repository is served over HTTPS
        info.include_packages.add('ca-certificates')
        collection = info.manifest.plugins['openvox'].get('collection', 'openvox8')
        # The repository suites are named after the Debian version, e.g. debian12
        line = ('deb [signed-by={keyring}] https://apt.voxpupuli.org debian{version} {collection}'
                .format(keyring=KEYRING_PATH, version=info.manifest.release.version, collection=collection))
        info.source_lists.add('openvox', line)


class InstallOpenVoxAptKey(Task):
    description = 'Installing the OpenVox APT signing key'
    phase = phases.package_installation
    predecessors = [apt.WriteSources]
    successors = [apt.AptUpdate]

    @classmethod
    def run(cls, info):
        from shutil import copy
        destination = os.path.join(info.root, KEYRING_PATH.lstrip('/'))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        copy(os.path.join(ASSETS_DIR, 'openvox.asc'), destination)
        os.chmod(destination, 0o644)


class AddOpenVoxAgentPackage(Task):
    description = 'Adding the OpenVox agent package'
    phase = phases.preparation

    @classmethod
    def run(cls, info):
        info.packages.add('openvox-agent')


class InstallModules(Task):
    description = 'Installing Puppet modules'
    phase = phases.system_modification

    @classmethod
    def run(cls, info):
        for module in info.manifest.plugins['openvox']['install_modules']:
            command = ['chroot', info.root, PUPPET_BINARY, 'module', 'install', '--force', str(module[0])]
            if len(module) == 2:
                command.extend(['--version', str(module[1])])
            log_check_call(command)


class CopyAssets(Task):
    description = 'Copying declared custom OpenVox assets.'
    phase = phases.system_modification
    predecessors = [InstallModules]

    @classmethod
    def run(cls, info):
        from bootstrapvz.common.tools import copy_tree
        copy_tree(info.manifest.plugins['openvox']['assets'], os.path.join(info.root, CONFIG_DIR))


class ApplyManifest(Task):
    description = 'Applying the Puppet manifest with OpenVox.'
    phase = phases.system_modification
    predecessors = [CopyAssets]

    @classmethod
    def run(cls, info):
        with open(os.path.join(info.root, 'etc/hostname'), encoding='utf-8') as handle:
            hostname = handle.read().strip()
        with open(os.path.join(info.root, 'etc/hosts'), 'a', encoding='utf-8') as handle:
            handle.write('127.0.0.1\t{hostname}\n'.format(hostname=hostname))
        from shutil import copy
        pp_manifest = info.manifest.plugins['openvox']['manifest']
        manifest_rel_dst = os.path.join('tmp', os.path.basename(pp_manifest))
        manifest_dst = os.path.join(info.root, manifest_rel_dst)
        copy(pp_manifest, manifest_dst)
        manifest_path = os.path.join('/', manifest_rel_dst)
        log_check_call(['chroot', info.root, PUPPET_BINARY, 'apply', '--verbose', '--debug', manifest_path])
        os.remove(manifest_dst)
        hosts_path = os.path.join(info.root, 'etc/hosts')
        sed_i(hosts_path, r'127.0.0.1\s*{hostname}\n?'.format(hostname=hostname), '')


class EnableAgent(Task):
    description = 'Enabling the OpenVox agent'
    phase = phases.system_modification

    @classmethod
    def run(cls, info):
        # openvox-agent ships the agent as puppet.service
        log_check_call(['chroot', info.root, 'systemctl', 'enable', 'puppet.service'])
