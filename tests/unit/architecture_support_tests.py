import os.path
import pytest
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.common.exceptions import ManifestError
from bootstrapvz.common.tools import config_get, load_data

manifests = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests')
azure = os.path.join(manifests, 'examples/azure/jessie.yml')
ec2_pvm = os.path.join(manifests, 'official/ec2/ebs-wheezy-amd64-pvm.yml')
gce = os.path.join(manifests, 'official/gce/buster.yml')
kvm = os.path.join(manifests, 'examples/kvm/buster-cloudimg.yml')
kvm_wheezy = os.path.join(manifests, 'examples/kvm/wheezy.yml')
oracle = os.path.join(manifests, 'official/oracle/jessie.yml')
virtualbox = os.path.join(manifests, 'examples/virtualbox/wheezy.yml')


def manifest_data(path, **system):
    data = load_data(path)
    data['system'].update(system)
    return data


@pytest.mark.parametrize('path,system', [
    (ec2_pvm, {'release': 'bookworm', 'architecture': 'arm64'}),
    (gce, {'architecture': 'i386'}),
    (kvm_wheezy, {'architecture': 'arm64', 'bootloader': 'none'}),
], ids=['ec2-arm64', 'gce-i386', 'kvm-wheezy-arm64'])
def test_architecture_without_kernel_rejected(path, system):
    with pytest.raises(ManifestError, match='no kernel') as excinfo:
        Manifest(path=path, data=manifest_data(path, **system))
    assert excinfo.value.data_path == ['system', 'architecture']


@pytest.mark.parametrize('bootloader', ['grub', 'extlinux'])
def test_arm64_rejected_with_bootloader(bootloader):
    with pytest.raises(ManifestError, match='i386 and amd64') as excinfo:
        Manifest(path=kvm, data=manifest_data(kvm, architecture='arm64', bootloader=bootloader))
    assert excinfo.value.data_path == ['system', 'bootloader']


@pytest.mark.parametrize('path', [azure, gce, oracle, virtualbox], ids=['azure', 'gce', 'oracle', 'virtualbox'])
def test_arm64_rejected_by_x86_only_providers(path):
    with pytest.raises(ManifestError):
        Manifest(path=path, data=manifest_data(path, architecture='arm64'))


def test_virtualbox_rejects_arm64():
    # The base validation already rejects grub and extlinux on arm64,
    # check that the provider itself restricts the architecture as well
    manifest = Manifest(path=virtualbox)
    data = manifest_data(virtualbox, architecture='arm64')
    with pytest.raises(ManifestError) as excinfo:
        manifest.modules['provider'].validate_manifest(data, manifest.schema_validator, manifest.validation_error)
    assert list(excinfo.value.data_path) == ['system', 'architecture']


@pytest.mark.parametrize('release', ['jessie', 'bookworm', 'trixie', 'stable', 'unstable'])
def test_kvm_arm64_allowed_without_bootloader(release):
    Manifest(path=kvm, data=manifest_data(kvm, release=release, architecture='arm64', bootloader='none'))


@pytest.mark.parametrize('release', ['bookworm', 'trixie', 'testing'])
def test_amd64_allowed(release):
    Manifest(path=gce, data=manifest_data(gce, release=release))


def kernel_table(tmp_path):
    path = tmp_path / 'packages-kernels.yml'
    path.write_text('bookworm:\n  amd64: linux-image-amd64\n', encoding='utf-8')
    return str(path)


def test_config_get(tmp_path):
    assert config_get(kernel_table(tmp_path), ['bookworm', 'amd64']) == 'linux-image-amd64'


@pytest.mark.parametrize('config_path,missing', [
    (['bookworm', 'arm64'], 'bookworm.arm64'),
    (['trixie', 'amd64'], 'trixie'),
])
def test_config_get_missing_key(tmp_path, config_path, missing):
    path = kernel_table(tmp_path)
    with pytest.raises(KeyError) as excinfo:
        config_get(path, config_path)
    assert excinfo.value.args[0] == '{path} has no entry for {key}'.format(path=path, key=missing)
