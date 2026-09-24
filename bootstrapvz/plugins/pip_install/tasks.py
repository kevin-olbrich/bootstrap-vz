from bootstrapvz.base import Task
from bootstrapvz.common import phases
from bootstrapvz.common.releases import bullseye, bookworm


class AddPipPackage(Task):
    description = 'Adding `pip\' and Co. to the image packages'
    phase = phases.preparation

    @classmethod
    def run(cls, info):
        # Debian dropped the Python 2 pip and headers in bullseye
        if info.manifest.release < bullseye:
            package_names = ('python-pip', 'build-essential', 'python-dev')
        else:
            package_names = ('python3-pip', 'build-essential', 'python3-dev')
        for package_name in package_names:
            info.packages.add(package_name)


class PipInstallCommand(Task):
    description = 'Install python packages from pypi with pip'
    phase = phases.system_modification

    @classmethod
    def run(cls, info):
        from bootstrapvz.common.tools import log_check_call
        packages = info.manifest.plugins['pip_install']['packages']
        pip = 'pip' if info.manifest.release < bullseye else 'pip3'
        pip_install_command = ['chroot', info.root, pip, 'install']
        if info.manifest.release >= bookworm:
            # The system Python is marked as externally managed (PEP 668) since bookworm,
            # installing into it is exactly what this plugin is for.
            pip_install_command.append('--break-system-packages')
        pip_install_command.extend(packages)
        log_check_call(pip_install_command)
