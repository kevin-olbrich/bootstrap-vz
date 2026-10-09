from bootstrapvz.base import Task
from bootstrapvz.common import phases
from bootstrapvz.common.tasks import apt
import os
import urllib.request
import urllib.error


def get_proxy_url(info):
    proxy_username = info.manifest.plugins['apt_proxy'].get('username')
    proxy_password = info.manifest.plugins['apt_proxy'].get('password')
    proxy_address = info.manifest.plugins['apt_proxy']['address']
    proxy_port = info.manifest.plugins['apt_proxy']['port']

    if None not in (proxy_username, proxy_password):
        proxy_auth = '{username}:{password}@'.format(
            username=proxy_username, password=proxy_password)
    else:
        proxy_auth = ''

    return 'http://{auth}{address}:{port}'.format(auth=proxy_auth, address=proxy_address, port=proxy_port)


class CheckAptProxy(Task):
    description = 'Checking reachability of APT proxy server'
    phase = phases.validation

    @classmethod
    def run(cls, info):
        proxy_address = info.manifest.plugins['apt_proxy']['address']
        proxy_port = info.manifest.plugins['apt_proxy']['port']
        proxy_url = 'http://{address}:{port}'.format(address=proxy_address, port=proxy_port)
        try:
            with urllib.request.urlopen(proxy_url, timeout=5):
                pass
        except urllib.error.URLError as e:
            # Default response from `apt-cacher-ng`
            # pylint: disable=no-member
            if isinstance(e, urllib.error.HTTPError) and e.code in [404, 406] and e.msg == 'Usage Information':
                pass
            else:
                import logging
                log = logging.getLogger(__name__)
                log.warning('The APT proxy server couldn\'t be reached. '
                            '`debootstrap\' and `apt-get\' commands may fail.')


class SetBootstrapProxy(Task):
    description = 'Setting proxy for debootstrap'
    phase = phases.preparation

    @classmethod
    def run(cls, info):
        # A proxy can not cache HTTPS downloads, so only http:// mirrors go through it
        info.bootstrap_env['http_proxy'] = get_proxy_url(info)


class SetAptProxy(Task):
    description = 'Setting proxy for APT'
    phase = phases.package_installation
    successors = [apt.AptUpdate]

    @classmethod
    def run(cls, info):
        proxy_path = os.path.join(info.root, 'etc/apt/apt.conf.d/02proxy')
        with open(proxy_path, 'w', encoding='utf-8') as proxy_file:
            proxy_file.write('Acquire::http {{ Proxy "{url}"; }};\n'.format(url=get_proxy_url(info)))


class RemoveAptProxy(Task):
    description = 'Removing APT proxy configuration file'
    phase = phases.system_cleaning

    @classmethod
    def run(cls, info):
        os.remove(os.path.join(info.root, 'etc/apt/apt.conf.d/02proxy'))
