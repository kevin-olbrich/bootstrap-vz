from bootstrapvz.base import Task
from bootstrapvz.common import phases
from bootstrapvz.providers.ec2.tasks import ami

import logging


class CopyAmiToRegions(Task):
    description = 'Copy AWS AMI over other regions'
    phase = phases.image_registration
    predecessors = [ami.RegisterAMI]

    @classmethod
    def run(cls, info):
        source_region = info._ec2['region']
        source_ami = info._ec2['image']['ImageId']
        name = info._ec2['ami_name']
        copy_description = "Copied from %s (%s)" % (source_ami, source_region)

        connect_args = {
            'aws_access_key_id': info.credentials['access-key'],
            'aws_secret_access_key': info.credentials['secret-key'],
            'aws_session_token': info.credentials.get('security-token', None),
        }

        region_amis = {source_region: source_ami}
        region_conns = {source_region: info._ec2['connection']}
        import boto3
        regions = info.manifest.plugins['ec2_publish'].get('regions', ())
        for region in regions:
            conn = boto3.client('ec2', region_name=region, **connect_args)
            region_conns[region] = conn
            copied_image = conn.copy_image(SourceRegion=source_region, SourceImageId=source_ami,
                                           Name=name, Description=copy_description)
            region_amis[region] = copied_image['ImageId']
        info._ec2['region_amis'] = region_amis
        info._ec2['region_conns'] = region_conns


class PublishAmiManifest(Task):
    description = 'Publish a manifest of generated AMIs'
    phase = phases.image_registration
    predecessors = [CopyAmiToRegions]

    @classmethod
    def run(cls, info):
        manifest_url = info.manifest.plugins['ec2_publish']['manifest_url']

        import json
        amis_json = json.dumps(info._ec2['region_amis'])

        from urllib.parse import urlparse
        parsed_url = urlparse(manifest_url)
        parsed_host = parsed_url.netloc
        if not parsed_url.scheme:
            with open(parsed_url.path, 'w') as local_out:
                local_out.write(amis_json)
        elif parsed_host.endswith('amazonaws.com') and 's3' in parsed_host:
            region = 'us-east-1'
            path = parsed_url.path[1:]
            if 's3-' in parsed_host:
                loc = parsed_host.find('s3-') + 3
                region = parsed_host[loc:parsed_host.find('.', loc)]

            if '.s3' in parsed_host:
                bucket = parsed_host[:parsed_host.find('.s3')]
            else:
                bucket, path = path.split('/', 1)

            import boto3
            conn = boto3.client('s3', region_name=region)
            conn.put_object(Bucket=bucket, Key=path, Body=amis_json,
                            ContentType='application/json', ACL='public-read')


class PublishAmi(Task):
    description = 'Make generated AMIs public'
    phase = phases.image_registration
    predecessors = [CopyAmiToRegions]

    @classmethod
    def run(cls, info):
        region_conns = info._ec2['region_conns']
        region_amis = info._ec2['region_amis']
        logger = logging.getLogger(__name__)

        import time
        for region, region_ami in region_amis.items():
            conn = region_conns[region]
            current_state = conn.describe_images(ImageIds=[region_ami])['Images'][0]['State']
            while current_state == 'pending':
                logger.debug('Waiting for %s in %s (currently: %s)', region_ami, region, current_state)
                time.sleep(5)
                current_state = conn.describe_images(ImageIds=[region_ami])['Images'][0]['State']
            conn.modify_image_attribute(ImageId=region_ami,
                                        LaunchPermission={'Add': [{'Group': 'all'}]})
