"""Remove the selected workspace's runtime while retaining its deployment data."""
import json

from host_services import remove_units
from installer import KEY, PROJECT, SOURCE, command_output, compose, load, operator_lock, run
from serve import Route, remove_matching, remove_workspace


def uninstall(data):
    manifest = data / 'compose.yaml'
    with operator_lock(data):
        settings_file = data / 'settings.json'
        if settings_file.is_file():
            settings = load(settings_file)
            project = settings['project']
            desktop = data / 'desktop-compose.yaml'
            if not PROJECT.fullmatch(project) or (manifest.is_file() and load(manifest)['name'] != project):
                raise ValueError('The Compose project does not match the deployment settings')
            if desktop.is_file() and load(desktop)['name'] != project + '-desktop':
                raise ValueError('The desktop Compose project does not match the deployment settings')
            registry = load(data / 'agents.json') if (data / 'agents.json').is_file() else {'agents': {}}
            remove_units(project)
            if (data / 'telegram').is_dir() and (data / '.env').is_file():
                run('docker', 'compose', '--env-file', str(data / '.env'), '-p', project + '-telegram',
                    '-f', str(SOURCE / 'integrations/telegram/compose.yaml'),
                    '--profile', 'agent', 'down', '--remove-orphans')
            if desktop.is_file():
                run('docker', 'compose', '-f', str(desktop), 'down', '--remove-orphans')
            if manifest.is_file():
                compose(data, '--profile', 'agents', 'down', '--remove-orphans')
            remove_workspace(settings, data, registry)
        elif manifest.is_file():
            document = load(manifest)
            prefix = 'constellation-remote-'
            key = document['name'].removeprefix(prefix)
            if (document['name'] == key or not KEY.fullmatch(key)
                    or set(document['services']) != {'engine', key}):
                raise ValueError('Select the data directory of a Constellation deployment')
            compose(data, 'down', '--remove-orphans')
            if '127.0.0.1:19444:19443' in document['services']['engine'].get('ports', []):
                tailnet = json.loads(command_output('tailscale', 'status', '--json'))
                host = ((tailnet.get('Self') or {}).get('DNSName') or '').rstrip('.')
                remove_matching(host, [Route(19444, 'tcp://127.0.0.1:19444', 'tcp')])
        else:
            raise ValueError('Select the data directory of a Constellation deployment')
    print(f'Removed workspace services. Deployment data and backups are in {data}.')
