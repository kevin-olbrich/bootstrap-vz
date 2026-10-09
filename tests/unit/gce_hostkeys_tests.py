import os.path

import pytest

from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common.tasks import ssh

from .. import recursive_glob

manifests = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../manifests/official/gce')
manifest_paths = sorted(recursive_glob(manifests, '*.yml'))


@pytest.mark.parametrize('manifest_path', manifest_paths,
                         ids=[os.path.relpath(path, manifests) for path in manifest_paths])
def test_build_hostkeys_shredded_and_regenerated_on_boot(manifest_path):
    tasks = load_tasks('resolve_tasks', Manifest(path=manifest_path))
    assert ssh.ShredHostkeys in tasks
    assert ssh.AddSSHKeyGeneration in tasks
