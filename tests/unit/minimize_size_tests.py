import os.path
from bootstrapvz.base.manifest import Manifest
from bootstrapvz.base.tasklist import load_tasks
from bootstrapvz.common.tasks import locale
from bootstrapvz.common.tools import load_data
from bootstrapvz.plugins.minimize_size.tasks import dpkg

example = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                       '../../manifests/examples/kvm/buster-cloudimg.yml')


def resolve_tasks(locales):
    data = load_data(example)
    data['plugins']['minimize_size']['dpkg'] = {'locales': locales}
    return load_tasks('resolve_tasks', Manifest(path=example, data=data))


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
