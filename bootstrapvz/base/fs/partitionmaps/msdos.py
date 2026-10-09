from .abstract import AbstractPartitionMap
from ..exceptions import PartitionError
from ..partitions.msdos import MSDOSPartition
from ..partitions.msdos_swap import MSDOSSwapPartition
from bootstrapvz.common.tools import log_check_call


class MSDOSPartitionMap(AbstractPartitionMap):
    """Represents a MS-DOS partition map
    Sometimes also called MBR (but that confuses the hell out of me, so ms-dos it is)
    """

    def __init__(self, data, sector_size, bootloader):
        """
        :param dict data: volume.partitions part of the manifest
        :param int sector_size: Sectorsize of the volume
        :param str bootloader: Name of the bootloader we will use for bootstrapping
        """
        from bootstrapvz.common.sectors import Sectors

        # List of partitions
        self.partitions = []

        # Returns the last partition unless there is none
        def last_partition():
            return self.partitions[-1] if self.partitions else None

        # The boot and swap partitions are optional
        if 'boot' in data:
            self.boot = MSDOSPartition(Sectors(data['boot']['size'], sector_size),
                                       data['boot']['filesystem'], data['boot'].get('format_command', None),
                                       data['boot'].get('mountopts', None), 'boot', last_partition())
            self.partitions.append(self.boot)

        if 'swap' in data:
            self.swap = MSDOSSwapPartition(Sectors(data['swap']['size'], sector_size), last_partition())
            self.partitions.append(self.swap)

        self.root = MSDOSPartition(Sectors(data['root']['size'], sector_size),
                                   data['root']['filesystem'], data['root'].get('format_command', None),
                                   data['root'].get('mountopts', None), 'root', last_partition())
        self.partitions.append(self.root)

        # Raise exception while trying to create additional partitions
        # as its hard to calculate the actual size of the extended partition ATM
        # And anyhow - we should go with GPT...
        for partition in data:
            if partition not in ["boot", "swap", "root", "type"]:
                raise PartitionError("If you want to have additional partitions please use GPT partition scheme")

        # Mark boot as the boot partition, or root, if boot does not exist
        getattr(self, 'boot', self.root).flags.append('boot')

        # If we are using the grub bootloader, we will need to add a 2 MB offset
        # at the beginning of the partitionmap and steal it from the first partition.
        # The MBR offset is included in the grub offset, so if we don't use grub
        # we should reduce the size of the first partition and move it by only 1MiB,
        # so that it is aligned.
        if bootloader == 'grub':
            mbr_offset = Sectors('2MiB', sector_size)
        else:
            mbr_offset = Sectors('1MiB', sector_size)

        self.partitions[0].pad_start += mbr_offset
        self.partitions[0].size -= mbr_offset

        super().__init__(bootloader)

    def _before_create(self, event):
        volume = event.volume
        # parted must use the partition boundaries computed in __init__ as they are,
        # they already take care of the alignment
        log_check_call(['parted', '--script', '--align', 'none', volume.device_path,
                        '--', 'mklabel', 'msdos'])
        # Create the partitions
        for partition in self.partitions:
            partition.create(volume)
