from bootstrapvz.base import Task
from bootstrapvz.common import phases
from bootstrapvz.common.tasks import apt
from bootstrapvz.common.tasks import packages
from bootstrapvz.common.tools import log_check_call
import os

# Name of the bootstrap key in /etc/apt/trusted.gpg.d, the extension depends on the key format
BOOTSTRAP_KEY_NAME = 'google-cloud-bootstrap'


class AddGoogleCloudRepoKey(Task):
    description = 'Adding Google Cloud Repo key.'
    phase = phases.package_installation
    predecessors = [apt.InstallTrustedKeys]
    successors = [apt.WriteSources]

    @classmethod
    def run(cls, info):
        key_file = os.path.join(info.root, 'google.gpg.key')
        log_check_call(['wget', 'https://packages.cloud.google.com/apt/doc/apt-key.gpg', '-O', key_file])
        # apt-key was removed in apt 3.0 (trixie), apt reads keys from trusted.gpg.d instead.
        # Files there must be named .asc when ASCII-armored and .gpg when binary.
        with open(key_file, 'rb') as key:
            armored = key.read(5) == b'-----'
        destination = os.path.join(info.root, 'etc/apt/trusted.gpg.d',
                                   BOOTSTRAP_KEY_NAME + ('.asc' if armored else '.gpg'))
        os.rename(key_file, destination)
        os.chmod(destination, 0o644)


class AddGoogleCloudRepoKeyringRepo(Task):
    description = 'Adding Google Cloud keyring repository.'
    phase = phases.preparation
    predecessors = [apt.AddManifestSources]

    @classmethod
    def run(cls, info):
        info.source_lists.add('google-cloud', 'deb http://packages.cloud.google.com/apt google-cloud-packages-archive-keyring-{system.release} main')


class InstallGoogleCloudRepoKeyringPackage(Task):
    description = 'Installing Google Cloud key package.'
    phase = phases.preparation
    successors = [packages.AddManifestPackages]

    @classmethod
    def run(cls, info):
        info.packages.add('google-cloud-packages-archive-keyring')


class CleanupBootstrapRepoKey(Task):
    description = 'Cleaning up bootstrap repo key.'
    phase = phases.system_cleaning

    @classmethod
    def run(cls, info):
        for extension in ('.asc', '.gpg'):
            key_path = os.path.join(info.root, 'etc/apt/trusted.gpg.d', BOOTSTRAP_KEY_NAME + extension)
            if os.path.exists(key_path):
                os.remove(key_path)
