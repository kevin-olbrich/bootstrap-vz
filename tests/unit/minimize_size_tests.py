import os.path
import re
import stat
import subprocess
import pytest
from bootstrapvz.base.bootstrapinfo import BootstrapInformation, DictClass
from bootstrapvz.base.fs.partitions import mount
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import create_list, get_all_tasks, load_tasks
from bootstrapvz.common import phases, tools
from bootstrapvz.common.exceptions import TaskError
from bootstrapvz.common.tasks import filesystem, locale, partitioning, volume
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.minimize_size.tasks import apt, dpkg, mounts, shrink

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/buster-cloudimg.yml')
vmdk_example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                            '../../manifests/examples/virtualbox/stretch-vagrant.yml')

# Files of the packages that debootstrap extracts, as `tar -t' lists them
PACKAGE_FILES = ['./usr/bin/ls',
                 './usr/share/doc/coreutils/README.gz',
                 './usr/share/locale/',
                 './usr/share/locale/locale.alias',
                 './usr/share/locale/de/LC_MESSAGES/coreutils.mo',
                 './usr/share/locale/de_AT/LC_MESSAGES/coreutils.mo',
                 './usr/share/locale/fr/LC_MESSAGES/coreutils.mo',
                 './usr/share/man/',
                 './usr/share/man/man1/ls.1.gz',
                 './usr/share/man/de/man1/ls.1.gz',
                 './usr/share/man/de_AT/man1/ls.1.gz',
                 './usr/share/man/fr/man1/ls.1.gz']


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    """Fails the test instead of running a command that the test did not mock"""
    def refuse(*args, **kwargs):
        raise AssertionError('runs a command: {args}'.format(args=args or kwargs))
    monkeypatch.setattr(tools, 'log_call', refuse)
    monkeypatch.setattr(tools, 'log_check_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)


def resolve_tasks(locales):
    data = load_data(example)
    data['plugins']['minimize_size']['dpkg'] = {'locales': locales}
    return load_tasks('resolve_tasks', Manifest(path=example, data=data))


def minimize_size_info(tmp_path, settings, path=example, backing=None):
    data = load_data(path)
    data['bootstrapper']['workspace'] = str(tmp_path)
    if backing is not None:
        data['volume']['backing'] = backing
    data['plugins'] = {'minimize_size': settings}
    # Tasks only use the attributes, a DictClass also lets pylint see the namespace of the plugin
    info = DictClass(vars(BootstrapInformation(Manifest(path=path, data=data))))
    info.root = str(tmp_path / 'root')
    os.makedirs(info.workspace)
    return info


def record_commands(monkeypatch, module):
    commands = []

    def log_check_call(command):
        commands.append(command)
        return []
    monkeypatch.setattr(module, 'log_check_call', log_check_call)
    return commands


def create_filter_scripts(info, tmp_path, filter_tasks):
    """Runs the tasks that set up the bootstrap filter and returns the dpkg configuration directory"""
    dpkg_config = tmp_path / 'root/etc/dpkg/dpkg.cfg.d'
    dpkg_config.mkdir(parents=True)
    dpkg.InitializeBootstrapFilterList.run(info)
    for task in filter_tasks:
        task.run(info)
    dpkg.CreateBootstrapFilterScripts.run(info)
    return dpkg_config


def grep_pattern(basic_regex):
    """Translates a basic regular expression of GNU grep into a Python one"""
    def translate(match):
        escaped, plain = match.groups()
        if plain:
            return '\\' + plain
        return escaped if escaped in '|+?(){}' else match.group(0)
    # | + ? ( ) { } are literal characters, and operators only with a backslash in front
    return re.compile(re.sub(r'\\(.)|([|+?(){}])', translate, basic_regex))


def bootstrap_filter(info):
    """Returns the PACKAGE_FILES that debootstrap leaves out, running the two greps of the filter script in Python"""
    with open(info._minimize_size['filter_script'], encoding='utf-8') as script:
        greps = re.search(r"grep '(.*)' \| grep --invert-match --fixed-strings '(.*)'", script.read(), re.DOTALL)
    exclude = grep_pattern(greps.group(1))
    keep = greps.group(2).split('\n')
    return [path for path in PACKAGE_FILES if exclude.search(path) and not any(fixed in path for fixed in keep)]


def test_empty_locales_drop_locales_package():
    tasks = resolve_tasks([])
    assert dpkg.FilterLocales in tasks
    assert locale.LocaleBootstrapPackage not in tasks
    assert locale.GenerateLocale not in tasks


def test_listed_locales_keep_locales_package():
    tasks = resolve_tasks(['en_US'])
    assert dpkg.FilterLocales in tasks
    assert locale.LocaleBootstrapPackage in tasks
    assert locale.GenerateLocale in tasks


def test_apt_is_configured_to_keep_little_data(tmp_path):
    info = minimize_size_info(tmp_path, {'apt': {'autoclean': True, 'languages': ['en', 'de'],
                                                 'gzip_indexes': True, 'autoremove_suggests': True}})
    apt_config = tmp_path / 'root/etc/apt/apt.conf.d'
    apt_config.mkdir(parents=True)
    for task in [apt.AutomateAptClean, apt.FilterTranslationFiles, apt.AptGzipIndexes, apt.AptAutoremoveSuggests]:
        task.run(info)
    settings = {path.name: [line for line in path.read_text(encoding='utf-8').splitlines()
                            if line and not line.startswith('#')]
                for path in apt_config.iterdir()}
    assert sorted(settings) == ['20autoremove-suggests', '20gzip-indexes', '20languages', '90clean']
    assert settings['20languages'] == ['Acquire::Languages { "en"; "de" };']
    assert settings['20gzip-indexes'] == ['Acquire::GzipIndexes "true";', 'Acquire::CompressionTypes::Order:: "gz";']
    assert settings['20autoremove-suggests'] == ['Apt::AutoRemove::SuggestsImportant "false";']
    assert 'Dir::Cache::pkgcache "";' in settings['90clean']
    assert 'Dir::Cache::srcpkgcache "";' in settings['90clean']


def test_locales_are_left_out_of_bootstrapped_and_installed_packages(tmp_path):
    info = minimize_size_info(tmp_path, {'dpkg': {'locales': ['de']}})
    dpkg_config = create_filter_scripts(info, tmp_path, [dpkg.FilterLocales])
    assert bootstrap_filter(info) == ['./usr/share/locale/de_AT/LC_MESSAGES/coreutils.mo',
                                      './usr/share/locale/fr/LC_MESSAGES/coreutils.mo',
                                      './usr/share/man/de_AT/man1/ls.1.gz',
                                      './usr/share/man/fr/man1/ls.1.gz']
    assert (dpkg_config / '10filter-locales').read_text(encoding='utf-8') == \
        ('path-exclude=/usr/share/locale/*\n'
         'path-include=/usr/share/locale/locale.alias\n'
         'path-include=/usr/share/locale/de/*\n')
    manpages = (dpkg_config / '10filter-manpages').read_text(encoding='utf-8').splitlines()
    assert manpages[0] == 'path-exclude=/usr/share/man/*'
    assert 'path-include=/usr/share/man/de/*' in manpages


def test_docs_are_left_out_of_bootstrapped_and_installed_packages(tmp_path):
    # Together with locales, as in the docker examples
    info = minimize_size_info(tmp_path, {'dpkg': {'locales': [], 'exclude_docs': True}})
    dpkg_config = create_filter_scripts(info, tmp_path, [dpkg.FilterLocales, dpkg.ExcludeDocs])
    assert bootstrap_filter(info) == ['./usr/share/doc/coreutils/README.gz',
                                      './usr/share/locale/de/LC_MESSAGES/coreutils.mo',
                                      './usr/share/locale/de_AT/LC_MESSAGES/coreutils.mo',
                                      './usr/share/locale/fr/LC_MESSAGES/coreutils.mo',
                                      './usr/share/man/de/man1/ls.1.gz',
                                      './usr/share/man/de_AT/man1/ls.1.gz',
                                      './usr/share/man/fr/man1/ls.1.gz']
    exclude_docs = (dpkg_config / '10exclude-docs').read_text(encoding='utf-8').splitlines()
    assert 'path-exclude=/usr/share/doc/*' in exclude_docs


def test_debootstrap_runs_every_package_through_the_filter(tmp_path):
    info = minimize_size_info(tmp_path, {'dpkg': {'locales': []}})
    create_filter_scripts(info, tmp_path, [dpkg.FilterLocales])
    filter_script = info._minimize_size['filter_script']
    assert os.access(filter_script, os.X_OK)
    # The scripts stay out of the image
    assert os.path.dirname(info.bootstrap_script) == info.workspace
    with open(info.bootstrap_script, encoding='utf-8') as script:
        bootstrap_script = script.read()
    [excludes_file] = re.findall(r'local excludes_file="([^"]+)"', bootstrap_script)
    assert os.path.dirname(excludes_file) == info.workspace
    assert 'tar -t | {script} > "$excludes_file"'.format(script=filter_script) in bootstrap_script
    assert 'tar --exclude-from "$excludes_file" -xf -' in bootstrap_script

    dpkg.DeleteBootstrapFilterScripts.run(info)
    assert not os.listdir(info.workspace)


def test_filter_does_not_replace_another_bootstrap_script(tmp_path):
    info = minimize_size_info(tmp_path, {'dpkg': {'locales': []}})
    info.bootstrap_script = '/usr/share/debootstrap/scripts/custom'
    dpkg.InitializeBootstrapFilterList.run(info)
    with pytest.raises(TaskError):
        dpkg.CreateBootstrapFilterScripts.run(info)
    assert info.bootstrap_script == '/usr/share/debootstrap/scripts/custom'


def test_temporary_data_is_written_to_the_workspace(tmp_path, monkeypatch):
    info = minimize_size_info(tmp_path, {})
    root = tmp_path / 'root'
    (root / 'tmp').mkdir(parents=True)
    os.chmod(root / 'tmp', 0o1777)
    (root / 'var/lib/apt/lists').mkdir(parents=True)
    # The root partition, as MountRoot leaves it
    info.volume.partition_map.root.fsm.current = 'mounted'
    info.volume.partition_map.root.mount_dir = info.root
    commands = record_commands(monkeypatch, mount)

    mounts.AddFolderMounts.run(info)
    folders = os.path.join(info.workspace, 'minimize_size')
    assert commands == [['mount', '--bind', os.path.join(folders, 'tmp'), str(root / 'tmp')],
                        ['mount', '--bind', os.path.join(folders, 'var_lib_apt_lists'), str(root / 'var/lib/apt/lists')]]
    # Programs in the image get the permissions of the directories that the mounts hide
    assert stat.S_IMODE(os.stat(os.path.join(folders, 'tmp')).st_mode) == 0o1777

    mounts.RemoveFolderMounts.run(info)
    assert commands[2:] == [['umount', str(root / 'tmp')], ['umount', str(root / 'var/lib/apt/lists')]]
    assert not os.path.exists(folders)


@pytest.mark.parametrize('settings, path, commands', [
    ({'zerofree': True}, example, ['zerofree']),
    ({'shrink': 'qemu-img'}, example, ['qemu-img']),
    ({'shrink': True}, vmdk_example, ['vmware-vdiskmanager']),
], ids=['zerofree', 'qemu-img', 'vmware-vdiskmanager'])
def test_tools_are_required_on_the_host(tmp_path, settings, path, commands):
    info = minimize_size_info(tmp_path, settings, path)
    for task in load_tasks('resolve_tasks', info.manifest):
        if task.phase == phases.validation and task.__module__ == shrink.__name__:
            task.run(info)
    assert sorted(info.host_dependencies) == commands


def test_volume_is_zeroed_and_shrunk_when_it_is_no_longer_in_use():
    manifest = Manifest(path=example)
    all_tasks = set(get_all_tasks([manifest.modules['provider']] + manifest.modules['plugins']))
    tasklist = create_list(load_tasks('resolve_tasks', manifest), all_tasks)
    assert (tasklist.index(filesystem.UnmountRoot) <
            tasklist.index(shrink.Zerofree) <
            tasklist.index(partitioning.UnmapPartitions))
    assert tasklist.index(volume.Detach) < tasklist.index(shrink.ShrinkVolumeWithQemuImg)
    # The order above must not be a coincidence of the topological sort
    assert filesystem.UnmountRoot in shrink.Zerofree.predecessors
    assert partitioning.UnmapPartitions in shrink.Zerofree.successors
    assert volume.Detach in shrink.ShrinkVolumeWithQemuImg.predecessors


def test_zerofree_runs_on_the_root_partition(tmp_path, monkeypatch):
    info = minimize_size_info(tmp_path, {'zerofree': True})
    info.volume.partition_map.root.device_path = '/dev/mapper/loop0p1'
    commands = record_commands(monkeypatch, shrink)
    shrink.Zerofree.run(info)
    assert commands == [['zerofree', '/dev/mapper/loop0p1']]


@pytest.mark.parametrize('backing, setting, compress', [
    ('qcow2', 'qemu-img', ['-c']),
    ('qcow2', 'qemu-img-no-compression', []),
    ('raw', 'qemu-img', []),
])
def test_image_is_shrunk_by_qemu_img(tmp_path, monkeypatch, backing, setting, compress):
    info = minimize_size_info(tmp_path, {'shrink': setting}, backing=backing)
    info.volume.image_path = os.path.join(info.workspace, 'volume.' + backing)
    with open(info.volume.image_path, 'wb') as image:
        image.write(b'volume')
    commands = []

    def qemu_img(command):
        commands.append(command)
        with open(command[-1], 'wb') as converted:
            converted.write(b'shrunk')
        return []
    monkeypatch.setattr(shrink, 'log_check_call', qemu_img)

    shrink.ShrinkVolumeWithQemuImg.run(info)
    [command] = commands
    converted = command[-1]
    assert command == ['qemu-img', 'convert', '-O', backing] + compress + [info.volume.image_path, converted]
    assert os.path.dirname(converted) == info.workspace
    # The converted image replaces the volume image
    assert os.listdir(info.workspace) == ['volume.' + backing]
    with open(info.volume.image_path, 'rb') as image:
        assert image.read() == b'shrunk'


def test_vmdk_is_shrunk_by_vmware_vdiskmanager(tmp_path, monkeypatch):
    info = minimize_size_info(tmp_path, {'shrink': 'vmware-vdiskmanager'}, vmdk_example)
    info.volume.image_path = os.path.join(info.workspace, 'volume.vmdk')
    with open(info.volume.image_path, 'wb') as image:
        image.write(b'volume')
    os.chmod(info.volume.image_path, 0o640)
    commands = []

    def vdiskmanager(command):
        commands.append(command)
        # The shrunk image is written with other permissions
        os.remove(command[-1])
        with open(command[-1], 'wb') as image:
            image.write(b'shrunk')
        os.chmod(command[-1], 0o600)
        return []
    monkeypatch.setattr(shrink, 'log_check_call', vdiskmanager)

    shrink.ShrinkVolumeWithVDiskManager.run(info)
    assert commands == [['/usr/bin/vmware-vdiskmanager', '-k', info.volume.image_path]]
    assert stat.S_IMODE(os.stat(info.volume.image_path).st_mode) == 0o640
