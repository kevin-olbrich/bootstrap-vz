from contextlib import contextmanager
import logging
log = logging.getLogger(__name__)


@contextmanager
def boot_image(manifest, settings, bootstrap_info):
    image_id = None
    try:
        import os
        from bootstrapvz.common.tools import log_check_call
        docker_machine = settings.get('run_settings', {}).get('docker', {}).get('machine', None)
        docker_env = os.environ.copy()
        if docker_machine is not None:
            cmd = ('eval "$(docker-machine env {machine})" && '
                   'echo $DOCKER_HOST && echo $DOCKER_CERT_PATH && echo $DOCKER_TLS_VERIFY'
                   .format(machine=docker_machine))
            [docker_host, docker_cert_path, docker_tls] = log_check_call([cmd], shell=True)
            docker_env['DOCKER_TLS_VERIFY'] = docker_tls
            docker_env['DOCKER_HOST'] = docker_host
            docker_env['DOCKER_CERT_PATH'] = docker_cert_path
            docker_env['DOCKER_MACHINE_NAME'] = docker_machine
        image_id = bootstrap_info._docker['image_id']

        from .image import Image
        with Image(image_id, docker_env) as container:
            yield container
    finally:
        if image_id is not None:
            log.debug('Deleting image')
            log_check_call(['docker', 'rmi', image_id], env=docker_env)
