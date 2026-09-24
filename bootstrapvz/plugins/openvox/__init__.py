from . import tasks

# Debian releases for which Vox Pupuli publishes openvox-agent packages
SUPPORTED_RELEASES = ('bullseye', 'bookworm', 'trixie')


def validate_manifest(data, validator, error):
    from bootstrapvz.common.tools import rel_path
    validator(data, rel_path(__file__, 'manifest-schema.yml'))

    from bootstrapvz.common.releases import get_release
    release = get_release(data['system']['release'])
    if release.codename not in SUPPORTED_RELEASES:
        msg = ('OpenVox is not available for Debian {release}, supported releases are: {supported}'
               .format(release=release.codename, supported=', '.join(SUPPORTED_RELEASES)))
        error(msg, ['system', 'release'])


def resolve_tasks(taskset, manifest):
    settings = manifest.plugins['openvox']
    taskset.add(tasks.AddOpenVoxAptSource)
    taskset.add(tasks.InstallOpenVoxAptKey)
    taskset.add(tasks.AddOpenVoxAgentPackage)
    if 'assets' in settings:
        taskset.add(tasks.CopyAssets)
    if 'manifest' in settings:
        taskset.add(tasks.ApplyManifest)
    if 'install_modules' in settings:
        taskset.add(tasks.InstallModules)
    if settings.get('enable_agent', False):
        taskset.add(tasks.EnableAgent)
