from bootstrapvz.base import Task
from bootstrapvz.common import phases
from bootstrapvz.common.tasks import grub


class AddVirtualConsoleGrubOutputDevice(Task):
    description = 'Adding `tty0\' as output device for grub'
    phase = phases.system_modification
    predecessors = [grub.SetGrubConsolOutputDeviceToSerial]
    successors = [grub.WriteGrubConfig]

    @classmethod
    def run(cls, info):
        info.grub_config['GRUB_CMDLINE_LINUX'].extend([
            'console=tty0',
            'consoleblank=0',
            'elevator=noop',
            'noibrs',
            'noibpb',
            'nopti',
            'nospectre_v2',
            'nospectre_v1',
            'l1tf=off',
            'nospec_store_bypass_disable',
            'no_stf_barrier',
            'mds=off',
            'mitigations=off',
        ])
