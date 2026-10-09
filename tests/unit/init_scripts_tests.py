import os.path
from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common.tasks import initd, ssh
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.expand_root import tasks as expand_root

# Without the cloud_init plugin, the EC2 provider installs every init script bootstrap-vz has
example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/official/ec2/ebs-jessie-amd64-hvm.yml')

SCRIPTS = ['expand-root', 'ec2-get-credentials', 'ec2-run-user-data']


def install_init_scripts(release, root, monkeypatch, plugins=None):
    """Runs the tasks that add and disable init scripts and returns the commands they issued"""
    data = load_data(example)
    data['system']['release'] = release
    del data['plugins']
    if plugins:
        data['plugins'] = plugins
    manifest = Manifest(path=example, data=data)

    commands = []

    def log_check_call(command):
        commands.append(command)
        return []
    monkeypatch.setattr(initd, 'log_check_call', log_check_call)
    monkeypatch.setattr(ssh, 'log_check_call', log_check_call)
    monkeypatch.setattr(expand_root, 'log_check_call', log_check_call)

    for path in ['etc/init.d', 'etc/systemd/system', 'usr/local/sbin']:
        os.makedirs(os.path.join(root, path))
    info = DictClass(manifest=manifest, root=str(root), initd={'install': {}, 'disable': []})
    for task in load_tasks('resolve_tasks', manifest):
        if initd.InstallInitScripts in task.successors:
            task.run(info)
    initd.InstallInitScripts.run(info)
    return commands


def unit_settings(path):
    settings = {}
    with open(path, encoding='utf-8') as unit:
        for line in unit:
            if '=' in line and not line.startswith('#'):
                key, value = line.strip().split('=', 1)
                settings.setdefault(key, []).append(value)
    return settings


def test_trixie_enables_native_units_only(tmp_path, monkeypatch):
    # systemd v260 removed systemd-sysv-install, so `systemctl enable` no longer finds SysV scripts
    commands = install_init_scripts('trixie', tmp_path, monkeypatch)
    enabled = [command[4] for command in commands if command[2:4] == ['systemctl', 'enable']]
    for name in enabled:
        unit = name if name.endswith('.service') else name + '.service'
        assert os.path.isfile(os.path.join(tmp_path, 'etc/systemd/system', unit)), name
    assert sorted(enabled) == sorted(SCRIPTS + ['ssh-generate-hostkeys.service'])
    assert not [command for command in commands if 'insserv' in command]


def test_trixie_units_run_the_init_scripts(tmp_path, monkeypatch):
    install_init_scripts('trixie', tmp_path, monkeypatch)
    for name in SCRIPTS:
        assert os.access(os.path.join(tmp_path, 'etc/init.d', name), os.X_OK)
        unit = unit_settings(os.path.join(tmp_path, 'etc/systemd/system', name + '.service'))
        assert unit['ExecStart'] == ['/etc/init.d/{name} start'.format(name=name)]
        assert unit['WantedBy'] == ['multi-user.target']
        # The units systemd generated for SysV scripts did not ignore SIGPIPE
        assert unit['IgnoreSIGPIPE'] == ['no']


def test_trixie_runs_user_data_only_once(tmp_path, monkeypatch):
    # The script disables itself with `insserv -r`, which cannot disable the native unit
    install_init_scripts('trixie', tmp_path, monkeypatch)
    unit = unit_settings(os.path.join(tmp_path, 'etc/systemd/system/ec2-run-user-data.service'))
    assert unit['ExecStartPost'] == ['/bin/systemctl --no-reload disable ec2-run-user-data.service']


def test_expand_root_plugin_replaces_the_common_script(tmp_path, monkeypatch):
    # Without this, the common expand-root unit overwrites the plugin's unit and its settings
    plugin = {'expand_root': {'filesystem_type': 'ext4', 'root_device': '/dev/xvda', 'root_partition': 1}}
    install_init_scripts('trixie', tmp_path, monkeypatch, plugins=plugin)
    unit = unit_settings(os.path.join(tmp_path, 'etc/systemd/system/expand-root.service'))
    assert unit['ExecStart'] == ['/usr/local/sbin/expand-root /dev/xvda 1 ext4']
    assert not os.path.exists(os.path.join(tmp_path, 'etc/init.d/expand-root'))


def test_trixie_units_keep_the_lsb_boot_order(tmp_path, monkeypatch):
    install_init_scripts('trixie', tmp_path, monkeypatch)
    units = os.path.join(tmp_path, 'etc/systemd/system')
    # Required-Start: $network
    get_credentials = unit_settings(os.path.join(units, 'ec2-get-credentials.service'))
    assert get_credentials['After'] == ['network-online.target']
    assert get_credentials['Wants'] == ['network-online.target']
    # Required-Start: ec2-get-credentials
    run_user_data = unit_settings(os.path.join(units, 'ec2-run-user-data.service'))
    assert run_user_data['After'] == ['ec2-get-credentials.service']
    # No Required-Start
    assert 'After' not in unit_settings(os.path.join(units, 'expand-root.service'))


def test_trixie_skips_the_ssh_host_key_init_script(tmp_path, monkeypatch):
    # ssh-generate-hostkeys.service runs the key generation, the init script would be redundant
    install_init_scripts('trixie', tmp_path, monkeypatch)
    assert not os.path.exists(os.path.join(tmp_path, 'etc/init.d/ssh-generate-hostkeys'))


def test_trixie_masks_disabled_init_scripts(tmp_path, monkeypatch):
    # Masking needs no unit file, so it also works without SysV script support
    commands = install_init_scripts('trixie', tmp_path, monkeypatch)
    assert ['chroot', str(tmp_path), 'systemctl', 'mask', 'hwclock.sh'] in commands


def test_jessie_registers_init_scripts_with_insserv(tmp_path, monkeypatch):
    commands = install_init_scripts('jessie', tmp_path, monkeypatch)
    root = str(tmp_path)
    scripts = SCRIPTS + ['generate-ssh-hostkeys']
    expected = ([['chroot', root, 'dpkg-query', '-W', 'openssh-server'],
                 ['chroot', root, 'insserv', '--remove', 'hwclock.sh']] +
                [['chroot', root, 'insserv', '--default', name] for name in scripts])
    assert sorted(commands) == sorted(expected)
    for name in scripts:
        assert os.access(os.path.join(tmp_path, 'etc/init.d', name), os.X_OK)
    assert not os.listdir(os.path.join(tmp_path, 'etc/systemd/system'))
