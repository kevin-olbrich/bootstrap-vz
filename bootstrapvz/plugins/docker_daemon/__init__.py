from bootstrapvz.common.tasks import apt
from bootstrapvz.common.tools import rel_path
from . import tasks

# Debian releases for which Docker publishes packages at download.docker.com
SUPPORTED_RELEASES = ('buster', 'bullseye', 'bookworm', 'trixie')


def validate_manifest(data, validator, error):
    validator(data, rel_path(__file__, 'manifest-schema.yml'))

    from bootstrapvz.common.releases import get_release
    release = get_release(data['system']['release'])
    if release.codename not in SUPPORTED_RELEASES:
        msg = ('Docker does not provide packages for Debian {release}, supported releases are: {supported}'
               .format(release=release.codename, supported=', '.join(SUPPORTED_RELEASES)))
        error(msg, ['system', 'release'])


def resolve_tasks(taskset, manifest):
    settings = manifest.plugins['docker_daemon']
    taskset.add(tasks.AddDockerAptSource)
    taskset.add(tasks.InstallDockerAptKey)
    taskset.add(tasks.AddDockerPackages)
    taskset.add(tasks.EnableMemoryCgroup)
    if 'version' in settings:
        taskset.add(tasks.PinDockerVersion)
        # Only added by default when the manifest has its own preferences
        taskset.add(apt.WritePreferences)
    if settings.get('docker_opts'):
        taskset.add(tasks.SetDockerOpts)
    if settings.get('pull_images', []):
        taskset.add(tasks.PullDockerImages)
