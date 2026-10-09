import os.path
import pytest
from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.ntp import tasks

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/buster-cloudimg.yml')

SERVERS = ['metadata.google.internal', 'time.example.org']

POOL_LINES = ('pool 0.debian.pool.ntp.org iburst\n'
              'pool 1.debian.pool.ntp.org iburst\n'
              'pool 2.debian.pool.ntp.org iburst\n'
              'pool 3.debian.pool.ntp.org iburst\n')

SERVER_LINES = ('server metadata.google.internal iburst\n'
                'server time.example.org iburst\n')

# Excerpt of the default /etc/ntp.conf of the ntp package before bookworm
NTP_CONF = ('driftfile /var/lib/ntp/ntp.drift\n'
            '\n'
            '# You do need to talk to an NTP server or two (or three).\n'
            '#server ntp.your-provider.example\n'
            '\n'
            '# pool.ntp.org maps to about 1000 low-stratum NTP servers.  Your server will\n'
            '# pick a different set every time it starts up.  Please consider joining the\n'
            '# pool: <http://www.pool.ntp.org/join.html>\n' +
            POOL_LINES +
            '\n'
            "# By default, exchange time with everybody, but don't allow configuration.\n"
            'restrict -4 default kod notrap nomodify nopeer noquery limited\n'
            'restrict -6 default kod notrap nomodify nopeer noquery limited\n')

# Excerpt of the default /etc/ntpsec/ntp.conf of the Debian ntpsec package
NTPSEC_TOS_LINES = ('tos maxclock 11\n',
                    'tos minclock 4 minsane 3\n')
NTPSEC_CONF = ('driftfile /var/lib/ntpsec/ntp.drift\n'
               'leapfile /usr/share/zoneinfo/leap-seconds.list\n'
               '\n'
               '# This should be maxclock 7, but the pool entries count towards maxclock.\n' +
               NTPSEC_TOS_LINES[0] +
               '\n'
               '# Comment this out if you have a refclock and want it to be able to discipline\n'
               '# the clock by itself (e.g. if the system is not connected to the network).\n' +
               NTPSEC_TOS_LINES[1] +
               '\n'
               '# Specify one or more NTP servers.\n'
               '\n'
               '# Public NTP servers supporting Network Time Security:\n'
               '# server time.cloudflare.com nts\n'
               '\n'
               '# pool.ntp.org maps to about 1000 low-stratum NTP servers.  Your server will\n'
               '# pick a different set every time it starts up.  Please consider joining the\n'
               '# pool: <https://www.pool.ntp.org/join.html>\n' +
               POOL_LINES +
               '\n'
               "# By default, exchange time with everybody, but don't allow configuration.\n"
               'restrict default kod nomodify noquery limited\n')


def ntp_info(release, root=None):
    data = load_data(example)
    data['system']['release'] = release
    data['plugins']['ntp'] = {'servers': list(SERVERS)}
    return DictClass(manifest=Manifest(path=example, data=data), packages=set(), root=str(root))


def write_config(root, path, content):
    config = root / path
    config.parent.mkdir(parents=True)
    config.write_text(content, encoding='utf-8')
    return config


@pytest.mark.parametrize('release', ['stretch', 'buster', 'bullseye'])
def test_ntp_installed_before_bookworm(release):
    info = ntp_info(release)
    tasks.AddNtpPackage.run(info)
    assert info.packages == {'ntp'}


@pytest.mark.parametrize('release', ['bookworm', 'trixie', 'forky', 'sid',
                                     'oldstable', 'stable', 'testing', 'unstable'])
def test_ntpsec_installed_from_bookworm(release):
    info = ntp_info(release)
    tasks.AddNtpPackage.run(info)
    assert info.packages == {'ntpsec'}


@pytest.mark.parametrize('release', ['buster', 'bullseye'])
def test_servers_set_in_ntp_conf_before_bookworm(release, tmp_path):
    config = write_config(tmp_path, 'etc/ntp.conf', NTP_CONF)
    tasks.SetNtpServers.run(ntp_info(release, tmp_path))
    assert config.read_text(encoding='utf-8') == NTP_CONF.replace(POOL_LINES, SERVER_LINES)


@pytest.mark.parametrize('release', ['bookworm', 'stable'])
def test_servers_set_in_ntpsec_conf_from_bookworm(release, tmp_path):
    config = write_config(tmp_path, 'etc/ntpsec/ntp.conf', NTPSEC_CONF)
    tasks.SetNtpServers.run(ntp_info(release, tmp_path))
    # minsane 3 would keep ntpd from ever setting the clock with fewer than three servers
    expected = NTPSEC_CONF.replace(POOL_LINES, SERVER_LINES)
    for tos_line in NTPSEC_TOS_LINES:
        expected = expected.replace(tos_line, '')
    assert config.read_text(encoding='utf-8') == expected
