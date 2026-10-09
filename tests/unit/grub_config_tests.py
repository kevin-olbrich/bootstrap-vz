import os.path
from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.common.tasks import grub


def write_grub_config(root, tasks):
    os.makedirs(os.path.join(root, 'etc/default'))
    info = DictClass(root=root)
    grub.InitGrubConfig.run(info)
    for task in tasks:
        task.run(info)
    grub.WriteGrubConfig.run(info)
    with open(os.path.join(root, 'etc/default/grub'), encoding='utf-8') as grub_defaults:
        return grub_defaults.read().splitlines()


def test_recovery_entries_disabled(tmp_path):
    # grub only reads the literal string "true", "True" leaves the recovery entries in place
    assert 'GRUB_DISABLE_RECOVERY="true"' in write_grub_config(str(tmp_path), [grub.DisableGrubRecovery])


def test_hidden_timeout_quiet(tmp_path):
    lines = write_grub_config(str(tmp_path), [grub.RemoveGrubTimeout])
    assert 'GRUB_HIDDEN_TIMEOUT_QUIET="true"' in lines
    assert 'GRUB_TIMEOUT=0' in lines
    assert 'GRUB_HIDDEN_TIMEOUT=0' in lines


def test_integers_and_lists(tmp_path):
    lines = write_grub_config(str(tmp_path), [grub.DisablePNIN])
    assert 'GRUB_DEFAULT=0' in lines
    assert 'GRUB_TIMEOUT=5' in lines
    assert 'GRUB_CMDLINE_LINUX="net.ifnames=0 biosdevname=0"' in lines
    # Empty lists are left out
    assert not any(line.startswith('GRUB_CMDLINE_LINUX_DEFAULT=') for line in lines)
