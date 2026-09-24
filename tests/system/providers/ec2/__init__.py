from contextlib import contextmanager
from tests.system.tools import waituntil
import logging
log = logging.getLogger(__name__)


@contextmanager
def prepare_bootstrap(manifest, build_server):
    if manifest.volume['backing'] == 's3':
        credentials = {'access-key': build_server.build_settings['ec2-credentials']['access-key'],
                       'secret-key': build_server.build_settings['ec2-credentials']['secret-key']}
        import boto3
        s3 = boto3.resource('s3', region_name=manifest.image['region'],
                            aws_access_key_id=credentials['access-key'],
                            aws_secret_access_key=credentials['secret-key'])
        log.debug('Creating S3 bucket')
        bucket_args = {'Bucket': manifest.image['bucket']}
        if manifest.image['region'] != 'us-east-1':
            bucket_args['CreateBucketConfiguration'] = {'LocationConstraint': manifest.image['region']}
        bucket = s3.create_bucket(**bucket_args)
        try:
            yield
        finally:
            log.debug('Deleting S3 bucket')
            bucket.objects.all().delete()
            bucket.delete()
    else:
        yield


@contextmanager
def boot_image(manifest, build_server, bootstrap_info, instance_type=None):

    credentials = {'access-key': build_server.run_settings['ec2-credentials']['access-key'],
                   'secret-key': build_server.run_settings['ec2-credentials']['secret-key']}
    import boto3
    ec2 = boto3.resource('ec2', region_name=bootstrap_info._ec2['region'],
                         aws_access_key_id=credentials['access-key'],
                         aws_secret_access_key=credentials['secret-key'])

    image_id = bootstrap_info._ec2['image']['ImageId']
    if manifest.volume['backing'] == 'ebs':
        from .images import EBSImage
        image = EBSImage(image_id, ec2)
    elif manifest.volume['backing'] == 's3':
        from .images import S3Image
        image = S3Image(image_id, ec2)
    else:
        raise ValueError('Unsupported volume backing: {backing}'.format(backing=manifest.volume['backing']))

    try:
        with run_instance(image, manifest, instance_type, ec2) as instance:
            yield instance
    finally:
        image.destroy()


@contextmanager
def run_instance(image, manifest, instance_type, ec2):

    with create_env(ec2) as boot_env:

        def waituntil_instance_is(state):
            def instance_has_state():
                instance.reload()
                return instance.state['Name'] == state
            return waituntil(instance_has_state, timeout=600, interval=3)

        def get_console_output():
            return instance.console_output().get('Output')

        instance = None
        try:
            log.debug('Booting ec2 instance')
            run_args = {'ImageId': image.ami.id,
                        'SubnetId': boot_env['subnet_id'],
                        'MinCount': 1,
                        'MaxCount': 1,
                        }
            if instance_type is not None:
                run_args['InstanceType'] = instance_type
            [instance] = ec2.create_instances(**run_args)
            instance.create_tags(Tags=[{'Key': 'Name', 'Value': 'bootstrap-vz test instance'}])

            if not waituntil_instance_is('running'):
                raise EC2InstanceStartupException('Timeout while booting instance')

            if not waituntil(lambda: get_console_output() is not None, timeout=600, interval=3):
                raise EC2InstanceStartupException('Timeout while fetching console output')

            from bootstrapvz.common.releases import wheezy
            if manifest.release <= wheezy:
                termination_string = 'INIT: Entering runlevel: 2'
            else:
                termination_string = 'Debian GNU/Linux'

            console_output = get_console_output()
            if termination_string not in console_output:
                last_lines = '\n'.join(console_output.split('\n')[-50:])
                message = ('The instance did not boot properly.\n'
                           'Last 50 lines of console output:\n{output}'.format(output=last_lines))
                raise EC2InstanceStartupException(message)

            yield instance
        finally:
            if instance is not None:
                log.debug('Terminating ec2 instance')
                instance.terminate()
                if not waituntil_instance_is('terminated'):
                    raise EC2InstanceStartupException('Timeout while terminating instance')
                # wait a little longer, aws can be a little slow sometimes and think the instance is still running
                import time
                time.sleep(15)


@contextmanager
def create_env(ec2):

    vpc_cidr = '10.0.0.0/28'
    subnet_cidr = '10.0.0.0/28'

    @contextmanager
    def vpc():
        log.debug('Creating VPC')
        vpc = ec2.create_vpc(CidrBlock=vpc_cidr)
        try:
            yield vpc
        finally:
            log.debug('Deleting VPC')
            vpc.delete()

    @contextmanager
    def subnet(vpc):
        log.debug('Creating subnet')
        subnet = ec2.create_subnet(VpcId=vpc.id, CidrBlock=subnet_cidr)
        try:
            yield subnet
        finally:
            log.debug('Deleting subnet')
            subnet.delete()

    with vpc() as _vpc:
        with subnet(_vpc) as _subnet:
            yield {'subnet_id': _subnet.id}


class EC2InstanceStartupException(Exception):
    pass
