from bootstrapvz.base import Task
from bootstrapvz.common import phases
from bootstrapvz.common.releases import bookworm


class AddNtpPackage(Task):
    description = 'Adding NTP Package'
    phase = phases.preparation

    @classmethod
    def run(cls, info):
        # Debian replaced ntp with ntpsec in bookworm, ntp is only a transitional package since then
        if info.manifest.release < bookworm:
            info.packages.add('ntp')
        else:
            info.packages.add('ntpsec')


class SetNtpServers(Task):
    description = 'Setting NTP servers'
    phase = phases.system_modification

    @classmethod
    def run(cls, info):
        import fileinput
        import os
        import re
        if info.manifest.release < bookworm:
            ntp_path = os.path.join(info.root, 'etc/ntp.conf')
        else:
            ntp_path = os.path.join(info.root, 'etc/ntpsec/ntp.conf')
        servers = list(info.manifest.plugins['ntp']['servers'])
        debian_ntp_server = re.compile(r'.*[0-9]\.debian\.pool\.ntp\.org.*')
        # The default `tos minclock 4 minsane 3` of ntpsec keeps ntpd from setting the clock with fewer than
        # three servers, so drop the tos lines like the DHCP hook of ntpsec does when it replaces the servers
        tos = re.compile(r'\s*tos\s')
        for line in fileinput.input(files=ntp_path, inplace=True, encoding='utf-8'):
            # Will write all the specified servers on the first match, then supress all other default servers
            if re.match(debian_ntp_server, line):
                while servers:
                    print('server {server_address} iburst'.format(server_address=servers.pop(0)))
            elif not re.match(tos, line):
                print(line, end='')
