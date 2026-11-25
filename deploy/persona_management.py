"""Register installed Constellation Instances in Persona Workshop."""
import json
from pathlib import Path
import stat
import subprocess
from urllib.parse import urlsplit
from uuid import UUID

TARGETS = '/api/v1/agent-targets'


def api(container, method, endpoint, payload=None):
    args = ['docker', 'exec', '-i', container, 'persona-workshop', '--home', '/data', 'api', method, endpoint]
    if payload is not None:
        args += ['--body-file', '/dev/stdin']
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else '',
                            text=True, capture_output=True, timeout=30, check=True)
    return json.loads(result.stdout)


def enroll(request, key, origin, credential, prior=None, *, reconnect=False):
    existing = [target for target in request('GET', TARGETS)['items'] if target['url'] == origin]
    if len(existing) > 1:
        raise ValueError(f'Workshop has multiple connections for {key}')
    if existing and prior and existing[0]['agent_id'] != prior:
        raise ValueError(f'Workshop encountered a different Instance for {key}')
    if existing and not reconnect:
        target = request('GET', f"{TARGETS}/{UUID(existing[0]['id'])}")
    else:
        target = request('POST', TARGETS, {'url': origin, **credential()})
    if prior and target['target']['agent_id'] != prior:
        raise ValueError(f'Workshop encountered a different Instance for {key}')
    return target


def private_token(path):
    if not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_mode & 0o077:
        raise ValueError('Use a dedicated management token file accessible only to its owner')
    value = path.read_text().strip()
    if not value or any(char.isspace() for char in value):
        raise ValueError('Invalid Instance management token')
    return value


def synchronize(data, registry, *, telegram=False, reconnect=None):
    from installer import RUNTIME_UID, compose, directory, load, save, up

    if not registry['agents'] and not telegram:
        return
    home = data / 'workshop-management'
    directory(home)
    mappings = home / 'targets.json'
    saved = load(mappings) if mappings.exists() else {}
    up(data, 'persona-workshop')
    container = compose(data, '--profile', 'agents', 'ps', '-q', 'persona-workshop',
                        capture_output=True, text=True).stdout.strip()
    if not container:
        raise ValueError('Persona Workshop is unavailable')

    def request(method, endpoint, body=None):
        return api(container, method, endpoint, body)

    entries = dict(registry['agents'])
    if telegram and (data / 'telegram/connector/config.json').exists():
        entries['telegram-agent'] = {'name': 'Telegram', 'placement': 'local'}
    roster = []
    for key, entry in sorted(entries.items()):
        if entry['placement'] == 'remote':
            binding = home / f'{key}.json'
            if not binding.exists():
                continue
            connection = load(binding)
            origin = connection.pop('url')
            credential = lambda: connection
        else:
            service = 'telegram-engine' if key == 'telegram-agent' else f'engine-{key}'
            origin = f'https://{service}:19443'

            def engine_command(*args):
                command = ['--profile', 'agents', 'exec'] if key != 'telegram-agent' else []
                if key == 'telegram-agent':
                    from installer import SOURCE, run
                    config = load(data / 'settings.json')
                    result = run('docker', 'compose', '-p', config['project'] + '-telegram', '-f',
                                 str(SOURCE / 'integrations/telegram/compose.yaml'), '--profile', 'agent',
                                 'exec', '-T', service, *args, capture_output=True, text=True)
                else:
                    result = compose(data, *command, '-T', service, *args, capture_output=True, text=True)
                return result.stdout

            def credential():
                path = home / f'{key}.token'
                if not path.exists():
                    record = json.loads(engine_command('agent-instance', '--home', '/instance', 'token', 'create',
                                                       '--name', 'Persona Workshop', '--scopes', 'observe,profiles',
                                                       '--expires-in', '25920000', '--local'))
                    save(path, record['token'] + '\n')
                return {'token': private_token(path), 'ca_pem': engine_command('cat', '/exchange/ca.pem')}

        previous = saved.get(key)
        try:
            target = enroll(request, key, origin, credential, previous and previous['instance'],
                            reconnect=key == reconnect)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if entry['placement'] != 'remote' or key == reconnect or not previous or 'public' not in previous:
                raise
            roster.append({**previous['public'], 'available': False})
            continue
        install = target['installation']
        public = {'key': key, 'name': entry['name'], 'target': target['target']['id'],
                  'revision': install['revision'], 'profile_active': bool(install.get('active')), 'available': True}
        saved[key] = {'instance': target['target']['agent_id'], 'target': target['target']['id'], 'public': public}
        save(mappings, saved)
        roster.append(public)
    if 'chief-of-staff' in entries:
        save(data / 'state/chief-of-staff/observer/workshop.json', roster, uid=RUNTIME_UID)
    return {entry['key']: entry for entry in roster}


def remember_remote(data, registry, key, origin, token_file, ca_file):
    from installer import directory, save, validated_url

    if registry['agents'].get(key, {}).get('placement') != 'remote':
        raise ValueError('Select a registered remote agent')
    parts = urlsplit(validated_url(origin))
    if parts.path not in {'', '/'} or parts.query:
        raise ValueError('Use the remote Instance HTTPS management origin')
    home = data / 'workshop-management'
    directory(home)
    value = {'url': origin.rstrip('/'), 'token': private_token(token_file)}
    if ca_file:
        value['ca_pem'] = ca_file.read_text()
    save(home / f'{key}.json', value)


def disconnect(data, key):
    from installer import compose, load, save

    home = data / 'workshop-management'
    mappings = home / 'targets.json'
    saved = load(mappings) if mappings.exists() else {}
    previous = saved.get(key)
    if previous:
        target = str(UUID(previous['target']))
        container = compose(data, '--profile', 'agents', 'ps', '-q', 'persona-workshop',
                            capture_output=True, text=True).stdout.strip()
        if not container:
            raise ValueError('Start Persona Workshop to complete worker retirement')
        matches = [row for row in api(container, 'GET', TARGETS)['items'] if row['id'] == target]
        if matches:
            item = matches[0]
            if item['agent_id'] != previous['instance'] or item['url'] != f'https://engine-{key}:19443':
                raise ValueError('The Workshop target no longer matches this worker')
            api(container, 'DELETE', f'{TARGETS}/{target}')
        del saved[key]
        save(mappings, saved)
    (home / f'{key}.token').unlink(missing_ok=True)
