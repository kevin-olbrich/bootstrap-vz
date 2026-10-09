import os.path
import pytest
from bootstrapvz.base.bootstrapinfo import BootstrapInformation
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.common import tools
from bootstrapvz.common.exceptions import ManifestError
from bootstrapvz.common.tasks import bootstrap
from bootstrapvz.common.tools import load_data

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/buster-cloudimg.yml')


def load_manifest(tmp_path, bootstrapper=None, release='buster'):
    data = load_data(example)
    data['system']['release'] = release
    data['bootstrapper'].update(bootstrapper or {}, workspace=str(tmp_path))
    return Manifest(path=example, data=data)


def debootstrap_command(manifest, tmp_path, monkeypatch):
    info = BootstrapInformation(manifest)
    info.root = str(tmp_path / 'root')
    calls = []
    monkeypatch.setattr(tools, 'log_check_call', lambda command, **kwargs: calls.append(command))
    bootstrap.Bootstrap.run(info)
    [command] = calls
    return command


def test_signature_check_is_forced_by_default(tmp_path, monkeypatch):
    command = debootstrap_command(load_manifest(tmp_path), tmp_path, monkeypatch)
    assert '--force-check-gpg' in command
    assert '--no-check-gpg' not in command


def test_forced_signature_check_can_be_turned_off(tmp_path, monkeypatch):
    manifest = load_manifest(tmp_path, {'force-check-gpg': False})
    command = debootstrap_command(manifest, tmp_path, monkeypatch)
    assert '--force-check-gpg' not in command
    assert '--no-check-gpg' not in command


def test_no_check_gpg_skips_the_forced_signature_check(tmp_path, monkeypatch):
    manifest = load_manifest(tmp_path, {'no-check-gpg': True})
    command = debootstrap_command(manifest, tmp_path, monkeypatch)
    assert '--no-check-gpg' in command
    assert '--force-check-gpg' not in command


def test_no_check_gpg_rejected_with_force_check_gpg(tmp_path):
    with pytest.raises(ManifestError, match='check-gpg'):
        load_manifest(tmp_path, {'no-check-gpg': True, 'force-check-gpg': True})


@pytest.mark.parametrize('bootstrapper', [{}, {'force-check-gpg': True}])
@pytest.mark.parametrize('release', ['wheezy', 'jessie'])
def test_signature_check_is_forced_before_stretch(tmp_path, monkeypatch, bootstrapper, release):
    manifest = load_manifest(tmp_path, bootstrapper, release)
    command = debootstrap_command(manifest, tmp_path, monkeypatch)
    assert '--force-check-gpg' in command
