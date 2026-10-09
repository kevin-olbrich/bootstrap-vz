from . import tasks
from bootstrapvz.common.tasks import initd
from bootstrapvz.common.tools import rel_path


def validate_manifest(data, validator, error):
    validator(data, rel_path(__file__, 'manifest-schema.yml'))


def resolve_tasks(taskset, manifest):
    taskset.add(tasks.InstallGrowpart)
    taskset.add(tasks.InstallExpandRootScripts)
    # This plugin replaces the common expand-root script, whose unit would overwrite the plugin's
    taskset.discard(initd.AddExpandRoot)
    taskset.discard(initd.AdjustExpandRootScript)
    taskset.discard(initd.AdjustGrowpartWorkaround)
