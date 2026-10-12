from bootstrapvz.base import Task
from bootstrapvz.common import phases
from bootstrapvz.common.tasks import grub


class ConfigureGrub(Task):
    description = 'Configuring grub for Google Compute Engine'
    phase = phases.system_modification
    successors = [grub.WriteGrubConfig]

    @classmethod
    def run(cls, info):
        info.grub_config['GRUB_CMDLINE_LINUX'].append('elevator=noop')
        # Enable SCSI block multiqueue on Stretch.
        from bootstrapvz.common.releases import stretch
        if info.manifest.release >= stretch:
            info.grub_config['GRUB_CMDLINE_LINUX'].append('scsi_mod.use_blk_mq=Y')
