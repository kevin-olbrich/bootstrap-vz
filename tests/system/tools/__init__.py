from contextlib import contextmanager
import logging
log = logging.getLogger(__name__)


@contextmanager
def boot_manifest(manifest_data, boot_vars={}):
    from bootstrapvz.common.tools import load_data
    settings = load_data('system-tests.yml')

    manifest_data = apply_build_settings(manifest_data, settings.get('build_settings', {}))
    from bootstrapvz.base.manifest import Manifest
    manifest = Manifest(data=manifest_data)

    import importlib
    provider_module = importlib.import_module('tests.system.providers.' + manifest.provider['name'])

    prepare_bootstrap = getattr(provider_module, 'prepare_bootstrap', noop)
    with prepare_bootstrap(manifest, settings):
        log.info('Building manifest')
        from bootstrapvz.base.main import run
        bootstrap_info = run(manifest)

        log.info('Creating and booting instance')
        with provider_module.boot_image(manifest, settings, bootstrap_info, **boot_vars) as instance:
            yield instance


def apply_build_settings(manifest_data, build_settings):
    if manifest_data['provider']['name'] == 'virtualbox' and 'guest_additions' in manifest_data['provider']:
        manifest_data['provider']['guest_additions'] = build_settings['guest_additions']
    if 'apt_proxy' in build_settings:
        manifest_data.get('plugins', {})['apt_proxy'] = build_settings['apt_proxy']
    if 'ec2-credentials' in build_settings:
        if 'credentials' not in manifest_data['provider']:
            manifest_data['provider']['credentials'] = {}
        for key in ['access-key', 'secret-key', 'certificate', 'private-key', 'user-id']:
            if key in build_settings['ec2-credentials']:
                manifest_data['provider']['credentials'][key] = build_settings['ec2-credentials'][key]
    if 's3-region' in build_settings and manifest_data['volume']['backing'] == 's3':
        if 'region' not in manifest_data['image']:
            manifest_data['image']['region'] = build_settings['s3-region']
    return manifest_data


def waituntil(predicate, timeout=5, interval=0.05):
    import time
    threshhold = time.time() + timeout
    while time.time() < threshhold:
        if predicate():
            return True
        time.sleep(interval)
    return False


def read_from_socket(socket_path, termination_string, timeout, read_timeout=0.5):
    import socket
    import select
    import errno
    console = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    console.connect(socket_path)
    console.setblocking(0)

    from timeit import default_timer
    start = default_timer()

    import codecs
    decoder = codecs.getincrementaldecoder('utf-8')('replace')
    output = ''
    ptr = 0
    continue_select = True
    while continue_select:
        read_ready, _, _ = select.select([console], [], [], read_timeout)
        if console in read_ready:
            while True:
                try:
                    output += decoder.decode(console.recv(1024))
                    if termination_string in output[ptr:]:
                        continue_select = False
                    else:
                        ptr = max(0, len(output) - len(termination_string))
                    break
                except OSError as e:
                    if e.errno != errno.EWOULDBLOCK:
                        raise
                    continue_select = False
        if default_timer() - start > timeout:
            from .exceptions import SocketReadTimeout
            msg = ('Reading from socket `{path}\' timed out after {seconds} seconds.\n'
                   'Here is the output so far:\n{output}'
                   .format(path=socket_path, seconds=timeout, output=output))
            raise SocketReadTimeout(msg)
    console.close()
    return output


@contextmanager
def noop(*args, **kwargs):
    yield
