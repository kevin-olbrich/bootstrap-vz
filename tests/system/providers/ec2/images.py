import logging
log = logging.getLogger(__name__)


class AmazonMachineImage:

    def __init__(self, image_id, ec2):
        self.ec2 = ec2
        self.ami = self.ec2.Image(image_id)


class EBSImage(AmazonMachineImage):

    def destroy(self):
        log.debug('Deleting AMI')
        snapshot_ids = [mapping['Ebs']['SnapshotId'] for mapping in self.ami.block_device_mappings
                        if 'SnapshotId' in mapping.get('Ebs', {})]
        self.ami.deregister()
        for snapshot_id in snapshot_ids:
            self.ec2.Snapshot(snapshot_id).delete()
        del self.ami


class S3Image(AmazonMachineImage):

    def destroy(self):
        log.debug('Deleting AMI')
        self.ami.deregister()
        del self.ami
