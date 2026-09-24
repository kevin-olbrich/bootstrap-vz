from bootstrapvz.base import Task
from bootstrapvz.common import phases
from bootstrapvz.common.tasks import apt
from bootstrapvz.common.tasks import grub
from bootstrapvz.common.tools import log_check_call, rel_path
import os
import shutil
import subprocess
import time

ASSETS_DIR = rel_path(__file__, 'assets')
# Location of Docker's APT signing key inside the image
KEYRING_PATH = '/etc/apt/keyrings/docker.asc'
DOCKER_PACKAGES = ['docker-ce', 'docker-ce-cli', 'containerd.io']


class AddDockerAptSource(Task):
    description = 'Adding the Docker APT repository'
    phase = phases.preparation
    predecessors = [apt.AddManifestSources]

    @classmethod
    def run(cls, info):
        # The repository is served over HTTPS
        info.include_packages.add('ca-certificates')
        line = ('deb [signed-by={keyring}] https://download.docker.com/linux/debian {codename} stable'
                .format(keyring=KEYRING_PATH, codename=info.manifest.release.codename))
        info.source_lists.add('docker', line)


class AddDockerPackages(Task):
    description = 'Adding the Docker packages'
    phase = phases.preparation

    @classmethod
    def run(cls, info):
        for package in DOCKER_PACKAGES:
            info.packages.add(package)


class PinDockerVersion(Task):
    description = 'Pinning the Docker version'
    phase = phases.preparation

    @classmethod
    def run(cls, info):
        version = info.manifest.plugins['docker_daemon']['version']
        info.preference_lists.add('docker', [{'package': 'docker-ce docker-ce-cli',
                                              'pin': 'version 5:{version}-*'.format(version=version),
                                              'pin-priority': 1001}])


class InstallDockerAptKey(Task):
    description = 'Installing the Docker APT signing key'
    phase = phases.package_installation
    predecessors = [apt.WriteSources]
    successors = [apt.AptUpdate]

    @classmethod
    def run(cls, info):
        destination = os.path.join(info.root, KEYRING_PATH.lstrip('/'))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copy(os.path.join(ASSETS_DIR, 'docker.asc'), destination)
        os.chmod(destination, 0o644)


class SetDockerOpts(Task):
    description = 'Setting the Docker daemon options'
    phase = phases.system_modification

    @classmethod
    def run(cls, info):
        docker_opts = info.manifest.plugins['docker_daemon']['docker_opts']
        dropin_dir = os.path.join(info.root, 'etc/systemd/system/docker.service.d')
        os.makedirs(dropin_dir, exist_ok=True)
        with open(os.path.join(dropin_dir, 'bootstrap-vz.conf'), 'w', encoding='utf-8') as dropin:
            # Replace the ExecStart of the docker-ce unit, appending the configured options
            dropin.write('[Service]\n'
                         'ExecStart=\n'
                         'ExecStart=/usr/bin/dockerd -H fd:// --containerd=/run/containerd/containerd.sock {opts}\n'
                         .format(opts=docker_opts))


class EnableMemoryCgroup(Task):
    description = 'Enable the memory cgroup in the grub config'
    phase = phases.system_modification
    successors = [grub.WriteGrubConfig]

    @classmethod
    def run(cls, info):
        info.grub_config['GRUB_CMDLINE_LINUX'].append('cgroup_enable=memory')


class PullDockerImages(Task):
    description = 'Pull docker images'
    phase = phases.system_modification

    @classmethod
    def run(cls, info):
        from bootstrapvz.common.exceptions import TaskError
        from subprocess import CalledProcessError
        images = info.manifest.plugins['docker_daemon'].get('pull_images', [])
        retries = info.manifest.plugins['docker_daemon'].get('pull_images_retries', 10)

        # Run the daemon installed in the image on the host, storing everything inside the image.
        # Only the image store is needed, so networking and iptables setup are disabled.
        bin_dir = os.path.join(info.root, 'usr/bin')
        env = os.environ.copy()
        # dockerd starts the containerd from the image when it is first in PATH
        env['PATH'] = bin_dir + os.pathsep + env.get('PATH', '')
        socket = 'unix://' + os.path.join(info.workspace, 'docker.sock')
        docker = [os.path.join(bin_dir, 'docker'), '-H', socket]

        dockerd = [os.path.join(bin_dir, 'dockerd'),
                   '--data-root', os.path.join(info.root, 'var/lib/docker'),
                   '--exec-root', os.path.join(info.workspace, 'docker-exec'),
                   '--pidfile', os.path.join(info.workspace, 'docker.pid'),
                   '--host', socket,
                   '--iptables=false',
                   '--bridge=none']
        with subprocess.Popen(dockerd, env=env) as daemon:
            try:
                # wait for the docker daemon to start
                for _ in range(retries):
                    try:
                        log_check_call(docker + ['version'])
                        break
                    except CalledProcessError:
                        time.sleep(1)
                else:
                    raise TaskError('The docker daemon did not start within {retries} seconds'.format(retries=retries))
                for img in images:
                    # docker load if tarball, docker pull if image name
                    if img.endswith('.tar.gz') or img.endswith('.tgz'):
                        cmd, action = docker + ['load', '--input', img], 'loading'
                    else:
                        cmd, action = docker + ['pull', img], 'pulling'
                    try:
                        log_check_call(cmd)
                    except CalledProcessError as e:
                        msg = 'error {e} {action} docker image {img}.'.format(e=e, action=action, img=img)
                        raise TaskError(msg) from e
            finally:
                # shut down the docker daemon
                daemon.terminate()
                try:
                    daemon.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    daemon.kill()
                    daemon.wait()
