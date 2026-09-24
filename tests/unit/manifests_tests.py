import os.path
from itertools import chain

import pytest

from .. import recursive_glob

manifests = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests')
manifest_paths = sorted(chain(recursive_glob(manifests, '*.yml'), recursive_glob(manifests, '*.json')))


@pytest.mark.parametrize('manifest_path', manifest_paths,
                         ids=[os.path.relpath(path, manifests) for path in manifest_paths])
def test_manifest_generator(manifest_path):
    """
    manifests_tests - test_manifest_generator.

    Loops through the manifests directory and tests that
    each file can successfully be loaded and validated.
    """
    from bootstrapvz.base.manifest import Manifest

    manifest = Manifest(path=manifest_path)
    assert manifest.data
    assert manifest.data['name']
    assert manifest.data['provider']
    assert manifest.data['bootstrapper']
    assert manifest.data['volume']
    assert manifest.data['system']
