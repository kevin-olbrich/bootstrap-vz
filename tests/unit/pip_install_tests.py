import os.path
import pytest
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.common.exceptions import ManifestError
from bootstrapvz.common.tools import load_data

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/buster-cloudimg.yml')

PLUGINS = ['pip_install', 'pip3_install']


def manifest_data(plugin, settings):
    data = load_data(example)
    data['plugins'][plugin] = settings
    return data


@pytest.mark.parametrize('plugin', PLUGINS)
@pytest.mark.parametrize('settings', [{},
                                      {'package': ['awscli']},
                                      {'packages': 'awscli'},
                                      {'packages': []}],
                         ids=['missing packages', 'misspelled key', 'string', 'empty list'])
def test_invalid_settings_rejected(plugin, settings):
    with pytest.raises(ManifestError) as excinfo:
        Manifest(path=example, data=manifest_data(plugin, settings))
    assert list(excinfo.value.data_path)[:2] == ['plugins', plugin]


@pytest.mark.parametrize('plugin', PLUGINS)
def test_valid_settings_accepted(plugin):
    Manifest(path=example, data=manifest_data(plugin, {'packages': ['awscli==1.3.13', 'boto3']}))
