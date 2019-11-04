from bootstrapvz.base import Task
from bootstrapvz.common import phases
from bootstrapvz.common.tasks import grub


class ConfigureGrub(Task):
    description = 'Change grub configuration to allow for ttyS0 output'
    phase = phases.system_modification
    successors = [grub.WriteGrubConfig]

    @classmethod
    def run(cls, info):
        info.grub_config['GRUB_CMDLINE_LINUX'].extend([
            'console=ttyS0,38400n8',
            'elevator=noop',
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
        # Enable SCSI block multiqueue on Stretch.
        from bootstrapvz.common.releases import stretch
        if info.manifest.release >= stretch:
            info.grub_config['GRUB_CMDLINE_LINUX'].append('scsi_mod.use_blk_mq=Y')
