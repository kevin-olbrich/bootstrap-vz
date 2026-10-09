from bootstrapvz.base import Task
from bootstrapvz.common import phases
from bootstrapvz.common.tasks import kernel
import os


class VirtIO(Task):
    description = 'Install virtio modules'
    phase = phases.system_modification
    successors = [kernel.UpdateInitramfs]

    @classmethod
    def run(cls, info):
        modules = os.path.join(info.root, 'etc/initramfs-tools/modules')
        with open(modules, "a", encoding='utf-8') as modules_file:
            modules_file.write("\n")
            for module in info.manifest.provider.get('virtio', []):
                modules_file.write(module + "\n")
