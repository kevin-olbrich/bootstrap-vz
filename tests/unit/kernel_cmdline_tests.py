import os.path
import pytest
from bootstrapvz.base.bootstrapinfo import DictClass
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.common import task_groups
from bootstrapvz.common.tasks import grub

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/buster-cloudimg.yml')

# Kernel parameters that turn off mitigations for CPU vulnerabilities
MITIGATION_OPT_OUTS = ['mitigations=off', 'noibrs', 'noibpb', 'nopti', 'nospectre_v1', 'nospectre_v2',
                       'l1tf=off', 'nospec_store_bypass_disable', 'no_stf_barrier', 'mds=off', 'tsx=on',
                       'tsx_async_abort=off']


def grub_kernel_parameters():
    manifest = Manifest(path=example)
    info = DictClass(manifest=manifest)
    grub.InitGrubConfig.run(info)
    # Run every task of the bootloader group that only edits the grub configuration
    for task in task_groups.get_bootloader_group(manifest):
        if grub.WriteGrubConfig in task.successors:
            task.run(info)
    return info.grub_config['GRUB_CMDLINE_LINUX'] + info.grub_config['GRUB_CMDLINE_LINUX_DEFAULT']


@pytest.mark.parametrize('parameter', MITIGATION_OPT_OUTS)
def test_cpu_vulnerability_mitigations_stay_enabled(parameter):
    assert parameter not in grub_kernel_parameters()
