import os.path
import pytest
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.common.exceptions import ManifestError
from bootstrapvz.common.tools import load_data

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/buster-cloudimg.yml')


def manifest_data(release, architecture):
    data = load_data(example)
    data['system']['release'] = release
    data['system']['architecture'] = architecture
    return data


@pytest.mark.parametrize('release', ['trixie', 'forky', 'duke', 'sid', 'stable', 'testing', 'unstable'])
def test_i386_rejected_from_trixie(release):
    with pytest.raises(ManifestError, match='i386'):
        Manifest(path=example, data=manifest_data(release, 'i386'))


@pytest.mark.parametrize('release', ['bookworm', 'oldstable'])
def test_i386_allowed_before_trixie(release):
    Manifest(path=example, data=manifest_data(release, 'i386'))


def test_amd64_allowed_from_trixie():
    Manifest(path=example, data=manifest_data('trixie', 'amd64'))
