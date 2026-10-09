import os.path
import pytest
from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import create_list, get_all_tasks, load_tasks
from bootstrapvz.common.tasks import network
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.openvox import tasks as openvox_tasks

# A docker manifest with variant minbase and without netbase
example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/docker/stretch.yml')


def minbase_manifest(hostname=None, include_packages=None):
    data = load_data(example)
    if hostname is not None:
        data['system']['hostname'] = hostname
    if include_packages is not None:
        data['bootstrapper']['include_packages'] = include_packages
    return Manifest(path=example, data=data)


def test_minbase_removes_host_dns_info():
    assert network.RemoveDNSInfo in load_tasks('resolve_tasks', minbase_manifest())


def test_minbase_without_hostname_removes_host_hostname():
    tasks = load_tasks('resolve_tasks', minbase_manifest())
    assert network.RemoveHostname in tasks
    assert network.SetHostname not in tasks


def test_minbase_with_hostname_sets_hostname():
    tasks = load_tasks('resolve_tasks', minbase_manifest(hostname='myhost'))
    assert network.SetHostname in tasks
    assert network.RemoveHostname not in tasks


@pytest.mark.parametrize('hostname', [None, 'myhost'])
def test_minbase_skips_interface_configuration(hostname):
    assert network.ConfigureNetworkIF not in load_tasks('resolve_tasks', minbase_manifest(hostname))


def test_minbase_with_netbase_configures_interfaces():
    tasks = load_tasks('resolve_tasks', minbase_manifest(include_packages=['netbase']))
    assert network.ConfigureNetworkIF in tasks


def test_openvox_applies_manifest_after_setting_hostname():
    # Without netbase, SetHostname cannot edit the /etc/hosts that ApplyManifest leaves behind
    data = load_data(example)
    data['system']['release'] = 'trixie'
    data['system']['hostname'] = 'myhost'
    data['plugins']['openvox'] = {'manifest': '/srv/site.pp'}
    manifest = Manifest(path=example, data=data)
    all_tasks = set(get_all_tasks([manifest.modules['provider']] + manifest.modules['plugins']))
    task_list = create_list(load_tasks('resolve_tasks', manifest), all_tasks)
    assert task_list.index(network.SetHostname) < task_list.index(openvox_tasks.ApplyManifest)


def set_hostname(root, hosts=None):
    os.makedirs(os.path.join(root, 'etc'))
    if hosts is not None:
        with open(os.path.join(root, 'etc/hosts'), 'w', encoding='utf-8') as hosts_file:
            hosts_file.write(hosts)
    info = DictClass(manifest=minbase_manifest(hostname='myhost'), manifest_vars={}, root=root)
    network.SetHostname.run(info)
    with open(os.path.join(root, 'etc/hostname'), encoding='utf-8') as hostname_file:
        assert hostname_file.read() == 'myhost'


def test_set_hostname_without_hosts_file(tmp_path):
    # Only netbase creates /etc/hosts, so a minbase tree without it has none
    set_hostname(str(tmp_path))
    assert not os.path.exists(os.path.join(str(tmp_path), 'etc/hosts'))


def test_set_hostname_adds_hostname_to_hosts_file(tmp_path):
    set_hostname(str(tmp_path), hosts='127.0.0.1\tlocalhost\n::1\t\tlocalhost\n')
    with open(os.path.join(str(tmp_path), 'etc/hosts'), encoding='utf-8') as hosts_file:
        assert hosts_file.read() == '127.0.0.1\tlocalhost\n127.0.1.1\tmyhost\n::1\t\tlocalhost\n'
