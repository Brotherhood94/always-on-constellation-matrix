"""Remove the selected workspace and optionally purge its stored data."""
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from host_services import remove_units
from installer import KEY, PROJECT, SOURCE, command_output, compose, load, operator_lock, run
from serve import Route, remove_matching, remove_workspace

MOUNT_INFO = Path('/proc/self/mountinfo')


def check_purge_mounts(data):
    for entry in MOUNT_INFO.read_text().splitlines():
        mount = Path(re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), entry.split()[4]))
        if mount.is_relative_to(data):
            raise ValueError(f'Unmount {mount} before purging the deployment data')


def purge_project_resources(project):
    label = f'label=com.docker.compose.project={project}'
    for list_args, remove_args in (
        (('ps', '-aq'), ('rm', '--force', '--volumes')),
        (('volume', 'ls', '-q'), ('volume', 'rm')),
        (('network', 'ls', '-q'), ('network', 'rm')),
    ):
        resources = command_output('docker', *list_args, '--filter', label).split()
        if resources:
            run('docker', *remove_args, *resources)


def uninstall(data, *, purge=False):
    if purge and not data.exists():
        print(f'Workspace data directory is already absent at {data}.')
        return
    if purge and SOURCE.is_relative_to(data):
        raise ValueError('Choose a deployment data directory outside the repository checkout')
    manifest = data / 'compose.yaml'
    down = ('down', '--remove-orphans', '--volumes') if purge else ('down', '--remove-orphans')
    with operator_lock(data):
        if purge:
            check_purge_mounts(data)
        settings_file = data / 'settings.json'
        if settings_file.is_file():
            settings = load(settings_file)
            project = settings['project']
            desktop = data / 'desktop-compose.yaml'
            if not PROJECT.fullmatch(project) or (manifest.is_file() and load(manifest)['name'] != project):
                raise ValueError('The Compose project does not match the deployment settings')
            if desktop.is_file() and load(desktop)['name'] != project + '-desktop':
                raise ValueError('The desktop Compose project does not match the deployment settings')
            registry_file = data / 'agents.json'
            registry = load(registry_file) if registry_file.is_file() else {'agents': {}}
            if purge and (not settings.get('server_name') or not registry_file.is_file()
                          or registry.get('local_server') != settings['server_name']):
                raise ValueError('Select the data directory of a Constellation deployment')
            for item in (data / 'twins').glob('*/job.json'):
                job = load(item)
                if job['status'] != 'stopped' and not job.get('cleaned'):
                    raise ValueError(f"Stop the digital twin and wait for cleanup before uninstalling: twin stop {job['id']}")
            projects = [project, project + '-telegram', project + '-desktop']
            if settings.get('systemd', True):
                remove_units(project)
            if (data / 'telegram').is_dir() and (data / '.env').is_file():
                run('docker', 'compose', '--env-file', str(data / '.env'), '-p', project + '-telegram',
                    '-f', str(SOURCE / 'integrations/telegram/compose.yaml'),
                    '--profile', 'agent', *down)
            if desktop.is_file():
                run('docker', 'compose', '-f', str(desktop), *down)
            if manifest.is_file():
                compose(data, '--profile', 'agents', *down)
            remove_workspace(settings, data, registry)
        elif manifest.is_file():
            document = load(manifest)
            prefix = 'constellation-remote-'
            key = document['name'].removeprefix(prefix)
            if (document['name'] == key or not KEY.fullmatch(key)
                    or set(document['services']) != {'engine', key}):
                raise ValueError('Select the data directory of a Constellation deployment')
            projects = [document['name']]
            compose(data, *down)
            if '127.0.0.1:19444:19443' in document['services']['engine'].get('ports', []):
                tailnet = json.loads(command_output('tailscale', 'status', '--json'))
                host = ((tailnet.get('Self') or {}).get('DNSName') or '').rstrip('.')
                remove_matching(host, [Route(19444, 'tcp://127.0.0.1:19444', 'tcp')])
        else:
            raise ValueError('Select the data directory of a Constellation deployment')
        if purge:
            for project in projects:
                purge_project_resources(project)
            with TemporaryDirectory(prefix='.constellation-purge-', dir=data.parent) as retired:
                data.rename(Path(retired) / 'deployment')
    if purge:
        print(f'Removed workspace services and permanently deleted deployment data at {data}.')
    else:
        print(f'Removed workspace services. Deployment data and backups are in {data}.')
