#!/usr/bin/env python3
"""Install and operate a private Matrix Constellation deployment."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
from shlex import quote
import stat
import subprocess
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

from agent_picker import choose_option, placement as agent_placement, prompt_text, select_integrations
from compose import BASE, CONNECTOR, ENGINE, SOURCE, definition, telegram_office_agent

sys.path.insert(0, str(SOURCE / 'deploy/matrix'))
from agent_catalog import KEY, PRIVATE_ROLES, profile
from connection_history import PUBLISHER, PUBLISHER_TOKEN, commit_registry, refresh_connections
from configure import write_config
from layout import CLIENT, create_layout, direct_room, invite, join, login, encoded
from provision import MatrixError, request

DATA = Path('/srv/constellation-matrix')
PRESETS = SOURCE / 'deploy/matrix/agent-profiles.json'
SERVER = re.compile(r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+')
OWNER = re.compile(r'[a-z0-9][a-z0-9._-]{0,63}')
MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{2,79}')
PROJECT = re.compile(r'[a-z][a-z0-9-]{0,38}')
RUNTIME_UID = 991
ROOT_UID = 992
RESERVED = {'monitor', 'dot', 'postgres', 'synapse', 'context', 'neo4j', 'persona-workshop',
            'telegram-agent', 'telegrambot', 'roster-inviter', 'root', 'vps-manager-ops'}
LEGACY_CONNECTION = 'chatgpt-codex'
PORTS = {'matrix_port': 18008, 'dashboard_https_port': 8443, 'neo4j_port': 17474,
         'neo4j_https_port': 8444, 'bolt_port': 17688, 'bolt_https_port': 17687,
         'context_port': 18081, 'context_https_port': 8445}


def load(path):
    return json.loads(path.read_text())


def starter_profiles():
    return load(SOURCE / 'deploy/matrix/starter-team.json')


def available_profiles(registry=None):
    # Keep saved identities and explicit legacy configurations usable on upgrade.
    return {**load(PRESETS), **starter_profiles(), **(registry or {}).get('agents', {})}


def save(path, value, mode=0o600, uid=None):
    raw = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False) + '\n'
    with NamedTemporaryFile(dir=path.parent, mode='w', encoding='utf-8', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
            if uid is not None:
                os.fchown(stream.fileno(), uid, uid)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def directory(path, mode=0o700, uid=None):
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ValueError('Expected a plain deployment directory')
    path.chmod(mode)
    if uid is not None:
        os.chown(path, uid, uid)


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def command_output(*args):
    return run(*args, stdout=subprocess.PIPE, text=True).stdout.strip()


def private_secret(path, uid=None):
    if not path.exists():
        save(path, secrets.token_urlsafe(32) + '\n', uid=uid)
    elif not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_mode & 0o077:
        raise ValueError('Existing credential must be a private regular file')
    return path.read_text().strip()


def validated_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or any(c.isspace() or c in '\'"' for c in url)):
        raise ValueError('External endpoints require HTTPS URLs without credentials, query strings, or fragments')
    if parsed.query:
        values = parse_qsl(parsed.query, strict_parsing=True)
        if len(values) != 1 or values[0][0] != 'connectURL':
            raise ValueError('Only a Neo4j connectURL parameter is supported')
        graph = urlsplit(values[0][1])
        if (graph.scheme != 'bolt+s' or graph.hostname != parsed.hostname or graph.username
                or graph.password or graph.query or graph.fragment or graph.path or not graph.port):
            raise ValueError('A browser shortcut must point to a TLS Bolt endpoint on the same host')
    return url


def valid_operator_username(owner, presets):
    return (isinstance(owner, str) and OWNER.fullmatch(owner) is not None
            and owner not in (RESERVED | presets.keys()))


def normalized(raw, presets):
    if raw.get('rootless'):
        raise ValueError('Use standard Docker Engine for this deployment')
    settings = {**PORTS, 'project': 'constellation-matrix', 'tailscale': True, 'systemd': True,
                'dot': False, 'telegram': False, **raw}
    if 'neo4j_password' in settings:
        raise ValueError('Use neo4j_password_file for Context Intelligence credentials')
    if 'neo4j_password_file' in settings and (
            not isinstance(settings['neo4j_password_file'], str) or not settings['neo4j_password_file']):
        raise ValueError('Provide a path to a private neo4j_password_file')
    if settings.pop('connection', LEGACY_CONNECTION) != LEGACY_CONNECTION:
        raise ValueError('Configure the provider in each agent’s Amplifier home')
    if not SERVER.fullmatch(settings.get('server_name', '')) or not PROJECT.fullmatch(settings['project']):
        raise ValueError('Use a DNS name and a lowercase Compose project name')
    if not valid_operator_username(settings.get('owner'), presets):
        raise ValueError('Use a valid operator username distinct from the agents')
    chosen = settings.get('agents')
    if not isinstance(chosen, list) or len(chosen) != len(set(chosen)) or any(key not in presets for key in chosen):
        raise ValueError('Specify a list of distinct available agents')
    weekly = settings.get('scout_weekly_utc', 'Mon 09:00')
    if weekly != 'off' and (not isinstance(weekly, str) or not re.fullmatch(r'(Mon|Tue|Wed|Thu|Fri|Sat|Sun) ([01][0-9]|2[0-3]):[0-5][0-9]', weekly)):
        raise ValueError('Use an English weekday and UTC time for scout_weekly_utc, or off')
    for key in ('tailscale', 'systemd', 'dot', 'telegram'):
        if type(settings[key]) is not bool:
            raise ValueError(f'{key} must be true or false')
    for key in PORTS:
        if type(settings[key]) is not int or not 1024 <= settings[key] <= 65535:
            raise ValueError(f'{key} must be a TCP port from 1024 to 65535')
    if len({settings[key] for key in PORTS}) != len(PORTS):
        raise ValueError('Deployment ports must be distinct')
    if 'subscriptions' in settings:
        subscriptions = settings['subscriptions']
        if (not isinstance(subscriptions, dict) or not isinstance(subscriptions.get('default'), str)
                or not subscriptions['default'].strip()):
            raise ValueError('Legacy subscription labels require a nonempty default')
        overrides = subscriptions.get('overrides', {})
        if not isinstance(overrides, dict) or any(
            key not in chosen or not isinstance(value, str) or not value.strip() for key, value in overrides.items()
        ):
            raise ValueError('Subscription overrides must identify selected agents and nonempty labels')
    server = settings['server_name']
    settings['dashboard_url'] = validated_url(raw.get('dashboard_url', f"https://{server}:{settings['dashboard_https_port']}/"))
    bolt = f"bolt+s://{server}:{settings['bolt_https_port']}"
    connection = urlencode({'connectURL': bolt}) if settings['tailscale'] else ''
    graph = f"https://{server}:{settings['neo4j_https_port']}/browser/"
    settings['context_url'] = validated_url(raw.get('context_url', graph + ('?' + connection if connection else '')))
    return settings


def env_write(data, values):
    # The Telegram operator tools use an explicitly exported, root-owned dotenv.
    save(data / '.env', ''.join(f'{key}={quote(str(value))}\n' for key, value in values.items()))


def base_environment(settings, data):
    server = settings['server_name']
    values = {'MATRIX_SERVER_NAME': server, 'MATRIX_URL': f'https://{server}', 'MATRIX_DATA': data,
              'MATRIX_LOCAL_PORT': settings['matrix_port'],
              'MATRIX_OWNER': f"@{settings['owner']}:{server}",
              'MATRIX_BACKUP_DIR': data / 'backups',
              'MATRIX_PROJECT': settings['project'],
              'MATRIX_REGISTRY': data / 'agents.json',
              'MATRIX_LOCAL_HOST': server,
              'MATRIX_SLICE': 'matrix-workload.slice' if settings['systemd'] else '',
              'TELEGRAM_MATRIX_NETWORK': settings['project'] + '_clients',
              'TELEGRAM_DATABASE_NETWORK': settings['project'] + '_database',
              'TELEGRAM_MEMORY_NETWORK': settings['project'] + '_memory',
              'TELEGRAM_WORKSHOP_NETWORK': settings['project'] + '_workshop-targets',
              'TELEGRAM_WORKSHOP_ENABLED': 'true' if settings['telegram'] else 'false',
              'TELEGRAM_DATA': data / 'telegram',
              'TELEGRAM_PROJECT': settings['project'] + '-telegram',
              'TELEGRAM_DOT_STATE': data / 'state/dot/matrix',
              'TELEGRAM_CONTEXT_TOKEN': data / 'context/telegram-agent.token',
              'TELEGRAM_CONTEXT_ENABLED': 'true' if settings['telegram'] else 'false'}
    one_time = data / 'secrets'
    directory(one_time)
    for key in ('POSTGRES_PASSWORD', 'MATRIX_REGISTRATION_SECRET', 'MATRIX_MACAROON_SECRET', 'MATRIX_FORM_SECRET'):
        values[key] = private_secret(one_time / key)
    os.environ.update({key: str(value) for key, value in values.items()})
    return values


def context_config(settings, data, registry):
    if registry['agents'] or settings['telegram']:
        directory(data / ('persona-workshop' if registry.get('team_version') == 2 else 'state/chief-of-staff/persona'), uid=RUNTIME_UID)
    directory(data / 'context')
    directory(data / 'context/data', uid=RUNTIME_UID)
    directory(data / 'context/graph', uid=7474)
    directory(data / 'context/config', mode=0o755)
    keys = {'observation-reader': ('agents-team', ['data:read']),
            PUBLISHER: ('agents-team', ['live:write', 'data:read'])}
    for key in registry['agents']:
        keys[key] = ('agents-team', ['live:write', 'data:read'] if key in PRIVATE_ROLES and registry['agents'][key].get('starter_role') == key else ['live:write'])
    if 'vps-manager' in registry['agents']:
        keys['vps-manager-ops'] = ('root-operations', ['live:write'])
    if settings['telegram']:
        keys['telegram-agent'] = ('telegram', ['live:write'])
    identities = {}
    grants = {}
    for key, (workspace, capabilities) in keys.items():
        if key == PUBLISHER:
            token = private_secret(data / PUBLISHER_TOKEN)
        else:
            token = private_secret(data / 'context' / (key + '.token'), uid=ROOT_UID if key in {'vps-manager', 'vps-manager-ops'} else RUNTIME_UID)
        identities[sha256(token.encode()).hexdigest()] = {'id': key}
        grants[key] = {'workspaces': [workspace], 'capabilities': capabilities}
    password = read_password_file(data / 'context/neo4j-password', 'Context Intelligence')
    # The host directory is private. The bind-mounted copy must be readable by Neo4j.
    save(data / 'context/neo4j-auth', f'neo4j/{password}\n', mode=0o444)
    save(data / 'context/config/server.json', {
        'auth_mode': 'static', 'allow_unauthenticated': False, 'access_control_mode': 'scoped',
        'api_keys_store_path': '/data/identity/api-keys.json', 'api_keys': identities,
        'contributor_grants': grants, 'neo4j_url': 'bolt://neo4j:7687', 'neo4j_user': 'neo4j',
        'neo4j_password': password,
    }, uid=RUNTIME_UID)


def compose(data, *args, **kwargs):
    return run('docker', 'compose', '--project-directory', str(data), *args, **kwargs)


def up(data, *services):
    if services:
        compose(data, '--profile', 'agents', 'up', '-d', '--wait', '--wait-timeout', '150', *services)


def refresh_context(data, *, retire=None):
    compose(data, 'stop', 'context')
    program = """import json
from pathlib import Path
import sys
from context_intelligence_server.identity_store import IdentityStore
config = json.loads(Path('/etc/context/server.json').read_text())
store = IdentityStore(Path(config['api_keys_store_path']))
store.load()
for digest, contributor in list(store.flat_dict.items()):
    if contributor in sys.argv[1:]:
        store.delete(digest)
for digest, entry in config['api_keys'].items():
    if store.flat_dict.get(digest) != entry['id']:
        if entry['id'] in store.flat_dict.values():
            raise RuntimeError('A managed contributor is already bound to a different key')
        store.put(digest, entry)
"""
    compose(data, 'run', '--rm', '--no-deps', '-T', '--entrypoint', '/app/.venv/bin/python',
            'context', '-B', '-c', program, *(([retire, 'vps-manager-ops'] if retire == 'vps-manager' else [retire]) if retire else []))
    up(data, 'context')


def password_error(value):
    if len(value) < 12:
        return 'Use an operator password of at least 12 characters'
    return None


def password(settings, file=None, *, confirm=False):
    path = file or settings.get('owner_password_file')
    if not path:
        if confirm:
            title = '🔐 Matrix operator password'
            notice = 'Save this password in a password manager before continuing. You need it to sign in to Element.'
        else:
            title = '🔐 Enter your existing Matrix operator password'
            notice = 'Use the same password you use to sign in to Element.'
        value = prompt_text(title, password_error, masked=True, notice=notice)
        if confirm:
            confirm_password('Matrix operator password', value)
        return value
    value = read_password_file(Path(path), 'Matrix operator')
    if error := password_error(value):
        raise ValueError(error)
    return value


def confirm_password(label, expected):
    prompt_text(f'🔑 Confirm {label}',
                lambda value: None if value == expected else 'Enter the same password again', masked=True)


def read_password_file(path, label):
    if not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_mode & 0o077:
        raise ValueError(f'The {label} password file must be private and a regular file')
    return path.read_text().rstrip('\r\n')


def prepare_graph_password(data, source, *, interactive):
    supplied = read_password_file(Path(source), 'Context Intelligence') if source else None
    save_graph_password(data, supplied, interactive=interactive)


def graph_password_error(value):
    if len(value) < 12 or not value.isprintable():
        return 'Use at least 12 printable characters for the Context Intelligence password'
    if '/' in value or value.startswith('-'):
        return 'Choose a Context Intelligence password without / or a leading -'
    return None


def prompt_graph_password():
    supplied = prompt_text('🧠 Context Intelligence password', graph_password_error, masked=True,
                           notice='Save this password for Neo4j Browser.')
    confirm_password('Context Intelligence password', supplied)
    return supplied


def save_graph_password(data, supplied, *, interactive=False):
    path = data / 'context/neo4j-password'
    if path.exists() or path.is_symlink():
        if supplied is not None and supplied != read_password_file(path, 'Context Intelligence'):
            raise ValueError('Use the existing Context Intelligence password for this workspace')
        return
    if (data / 'context/graph/data').exists() or (data / 'context/graph/databases').exists():
        raise ValueError('Recover the existing Context Intelligence password before reinstalling')

    directory(path.parent)
    if interactive:
        supplied = prompt_graph_password()
    if supplied is None:
        save(path, secrets.token_hex(32) + '\n')
        return
    if error := graph_password_error(supplied):
        raise ValueError(error)
    save(path, supplied + '\n')


def docker_build(tag, directory, *, dockerfile=None):
    args = ['docker', 'build', '-t', tag]
    if dockerfile:
        args += ['-f', str(dockerfile)]
    run(*args, str(directory))


def prepare_images(settings, registry):
    if registry['agents'] or settings['dot']:
        docker_build(CONNECTOR, SOURCE / 'agents/connector')
    if any(entry['placement'] == 'local' for entry in registry['agents'].values()):
        docker_build(BASE, SOURCE / 'integrations/telegram/engine')
        docker_build(ENGINE, SOURCE, dockerfile=SOURCE / 'agents/engine/Dockerfile')


def register_entry(settings, registry, key, definition):
    if not KEY.fullmatch(key) or len(key) > 36 or key in RESERVED or key == settings['owner'] or key.startswith(('engine-', 'acp-')):
        raise ValueError('Invalid or reserved agent ID')
    if key in registry.get('retired', {}):
        raise ValueError(f'Agent {key} was retired. Use a new ID or remove it from the install config.')
    name, model, effort = (definition[field] for field in ('name', 'model', 'effort'))
    if not name or len(name) > 64 or not name.isprintable() or not MODEL.fullmatch(model):
        raise ValueError('Agent requires a printable name and a valid provider model')
    if effort not in {'none', 'minimal', 'low', 'medium', 'high', 'xhigh'}:
        raise ValueError('Invalid reasoning effort')
    placement = agent_placement(key, definition)
    if placement not in {'local', 'remote'} or (key in (PRIVATE_ROLES | {'task-manager', 'chief-of-staff', 'vps-manager'}) and placement != 'local'):
        raise ValueError('Invalid agent placement')
    validated = profile(definition['profile'])
    if validated['role'] == 'observer' and key != 'chief-of-staff':
        raise ValueError('The observer role belongs to Chief of Staff')
    previous = registry['agents'].get(key)
    if previous and previous.get('retirement'):
        raise ValueError(f'Agent {key} is retiring. Resume its removal first.')
    labels = settings.get('subscriptions', {})
    default_label = (previous or {}).get('subscription')
    label = labels.get('overrides', {}).get(key, labels.get('default', default_label))
    subscription = definition.get('subscription', label)
    if subscription is not None and (not isinstance(subscription, str)
                                     or not subscription.strip() or not subscription.isprintable()):
        raise ValueError('Use a printable subscription label')
    server = definition.get('server', settings['server_name'] if placement == 'local' else 'remote')
    if not isinstance(server, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}', server):
        raise ValueError('Invalid agent host label')
    if any(other != key and (entry['name'] == name or
           (validated['role'] == 'coordinator' and entry['profile']['role'] == 'coordinator'))
           for other, entry in registry['agents'].items()):
        raise ValueError('Agent display names must be unique and only one coordinator can be installed')
    desired = {'name': name, 'server': server, 'placement': placement, 'model': model,
               'effort': effort, 'profile': validated, 'active': False}
    if registry.get('team_version') == 2 and key in starter_profiles():
        desired['starter_role'] = key
    if subscription is not None:
        desired['subscription'] = subscription
    if previous:
        existing = {**previous, 'active': False}
        existing.pop('amplifier', None)
        if existing != desired:
            raise ValueError(f'Agent {key} is already provisioned with different settings')
    registry['agents'][key] = previous or desired


def prepare_agent_state(data, key, entry, rooms, owner_token, settings):
    server = settings['server_name']
    layout = data / 'state' / key
    matrix_state = layout / 'matrix'
    directory(layout)
    gateway_uid = ROOT_UID if key == 'vps-manager' else RUNTIME_UID
    directory(matrix_state, uid=gateway_uid)
    if key != 'dot':
        for name in ('work', 'instance', 'exchange', 'tool-credentials'):
            directory(layout / name, uid=RUNTIME_UID)
    if key not in {'dot', 'vps-manager', 'chief-of-staff'}:
        directory(layout / 'work/desktop', uid=RUNTIME_UID)
    peer = f'@{key}:{server}'
    if key == 'vps-manager':
        directory(layout / 'tool-credentials', mode=0o750, uid=RUNTIME_UID)
        os.chown(layout / 'tool-credentials', RUNTIME_UID, ROOT_UID)
        directory(layout / 'gateway-credentials', mode=0o750)
        os.chown(layout / 'gateway-credentials', 0, ROOT_UID)
    credential = matrix_state / 'matrix-password'
    secret = private_secret(credential, uid=gateway_uid)
    token_file = layout / 'gateway-credentials/matrix-token' if key == 'vps-manager' else matrix_state / 'matrix-token'
    device = 'VPSMANAGER' if key == 'vps-manager' else 'MATRIX' + key.upper().replace('-', '')
    if not token_file.exists():
        own = login(key, secret, device, entry['name'])
        save(token_file, own['access_token'] + '\n', uid=None if key == 'vps-manager' else gateway_uid)
        if key == 'vps-manager':
            os.chown(token_file, 0, ROOT_UID)
            token_file.chmod(0o440)
    token = token_file.read_text().strip()
    who = request('GET', CLIENT + '/account/whoami', token=token)
    if who.get('user_id') != peer or who.get('device_id') != device:
        raise ValueError('Agent Matrix session does not match its registry identity')
    invite(rooms['office'], peer, owner_token)
    join(rooms['office'], peer, token)
    room = rooms['office']
    if key in {'vps-manager', 'chief-of-staff'}:
        label = 'Root Operations' if key == 'vps-manager' else entry['name']
        room = direct_room(server, rooms['space'], 'root' if key == 'vps-manager' else key, label, rooms['owner'], owner_token, peer, token)
    save(matrix_state / 'room-id', room + '\n', uid=gateway_uid)
    if key in PRIVATE_ROLES and entry.get('starter_role') == key:
        private = direct_room(server, rooms['space'], key, entry['name'], rooms['owner'], owner_token, peer, token)
        save(matrix_state / 'control-room-id', private + '\n', uid=gateway_uid)
    if key == 'dot':
        private_secret(matrix_state / 'bridge-token', uid=RUNTIME_UID)
    if key == 'chief-of-staff':
        directory(layout / 'observer', uid=RUNTIME_UID)
        directory(layout / 'persona', uid=RUNTIME_UID)
        directory(layout / 'observer/outbox', uid=RUNTIME_UID)
        save(layout / 'observer/agents-room-id', rooms['office'] + '\n', uid=RUNTIME_UID)
    if key == 'vps-manager':
        save(layout / 'config.json', {'url': 'http://synapse:8008', 'owner': rooms['owner'], 'root': peer,
                                      'agents': rooms['office'], 'audit': room, 'gateway_uid': ROOT_UID,
                                      'legacy_cutover': (data / 'vps-manager-migration.json').is_file(),
                                      'dot': f'@dot:{server}' if settings['dot'] else None},
             mode=0o444)
        operations = load(SOURCE / 'integrations/root/operations.json')
        for detail in operations.values():
            detail['argv'][-1] = detail['argv'][-1].replace('matrix-', settings['project'] + '-', 1)
        save(layout / 'operations.json', operations, mode=0o444)
        save(layout / 'approved-scripts.json', {'matrix_port': settings['matrix_port']})
        directory(layout / 'execution')


def close_office_setup_notice(data):
    path = data / 'bot/office-setup.json'
    if path.is_file():
        office = load(data / 'rooms.json')['office']
        save(path, {'room': office, 'pending': False}, uid=RUNTIME_UID)


def provision_layout(settings, data, registry, passphrase, *, unattended=False, office_setup=False):
    from roster import prepare_management

    own = login(settings['owner'], passphrase)
    token = own['access_token']
    try:
        monitor = login('monitor', private_secret(data / 'bot/password', uid=RUNTIME_UID))
        request('POST', CLIENT + '/logout', {}, monitor['access_token'])
        rooms = create_layout(settings['server_name'], own['user_id'], token)
        save(data / 'bot/room-id', rooms['alerts'] + '\n', uid=RUNTIME_UID)
        if office_setup:
            notice = data / 'bot/office-setup.json'
            if not notice.exists():
                invite(rooms['office'], f"@monitor:{settings['server_name']}", token)
                save(notice, {'room': rooms['office'], 'pending': True,
                              'command': workspace_command(data, elevated=False) + ' setup-guide'}, uid=RUNTIME_UID)
        for key, entry in registry['agents'].items():
            prepare_agent_state(data, key, entry, rooms, token, settings)
        if settings['dot']:
            prepare_agent_state(data, 'dot', {'name': 'Dot'}, rooms, token, settings)
        if settings['telegram']:
            provision_telegram_layout(settings, data, rooms, token, unattended=unattended)
        save(data / 'rooms.json', rooms)
        prepare_management(data, settings, registry, token)
    finally:
        request('POST', CLIENT + '/logout', {}, token)


def compose_config(settings, data, registry):
    if 'root' in registry['agents']:
        raise ValueError('Run agent migrate-root to replace the legacy Root agent before changing the deployment.')
    if settings['telegram']:
        directory(data / 'telegram', 0o711)
        directory(data / 'telegram/office', uid=RUNTIME_UID)
    environment = base_environment(settings, data)
    env_write(data, environment)
    context_config(settings, data, registry)
    manifest = data / 'compose.yaml'
    networks = load(manifest).get('networks', {}) if manifest.exists() else {}
    workshop_internal = networks.get('workshop-targets', {}).get('internal', True)
    save(manifest, definition(settings, data, registry, environment, workshop_internal=workshop_internal))
    return environment


def publish_dashboard(settings, data):
    directory(data / 'dashboard', 0o755)
    for name in ('dashboard.css', 'connections.mjs', 'dashboard.mjs', 'index.html'):
        save(data / 'dashboard' / name, (SOURCE / 'dashboard' / name).read_text(), mode=0o644)
    save(data / 'dashboard/links.json', {'dashboard': settings['dashboard_url'], 'context': settings['context_url']},
         mode=0o644)


def prepare_storage(settings, data):
    directory(data, 0o711)
    for name in ('postgres', 'monitor', 'monitor/outbox', 'state', 'backups', 'staging'):
        directory(data / name, 0o750)
    os.chown(data / 'monitor/outbox', 0, RUNTIME_UID)
    for name in ('synapse', 'bot', 'monitor/acks', 'desktop'):
        directory(data / name, uid=RUNTIME_UID)
    publish_dashboard(settings, data)


def publish_inventory(settings, data, registry):
    from agent_catalog import document, workshop_profiles
    from operator_cli import publish_metadata
    observed = []
    result = compose(data, '--profile', 'agents', 'ps', '-a', '--format', 'json', stdout=subprocess.PIPE, text=True)
    raw = result.stdout.strip()
    rows = json.loads(raw) if raw.startswith('[') else [json.loads(line) for line in raw.splitlines()]
    health = {row['Service']: row for row in rows}
    integrations = {}
    for key in ('synapse', 'context', 'neo4j', 'dot', 'monitor', 'persona-workshop'):
        if key in health or key in {'synapse', 'context', 'neo4j', 'monitor'}:
            row = health.get(key, {})
            integrations[key] = row.get('Health') or row.get('State') or 'unavailable'
    if settings['telegram']:
        integrations['Telegram'] = 'configured' if (data / 'telegram/connector/config.json').exists() else 'setup required'
    for key, entry in registry['agents'].items():
        row = health.get(key, {})
        status = 'Registered' if entry['placement'] == 'remote' else 'Unavailable'
        if row.get('State') == 'running' and entry['active']:
            status = {'healthy': 'Available', 'starting': 'Starting', 'unhealthy': 'Unavailable'}.get(row.get('Health'), 'Running')
        observed.append({'name': entry['name'], 'server': entry['server'], 'status': status})
    now = int(time.time())
    save(data / 'dashboard/agents.json', document(registry, observed, settings['server_name'], now, workshop_profiles(data)), mode=0o644)
    save(data / 'dashboard/integrations.json', {'time': now, 'services': integrations}, mode=0o644)
    publish_metadata(data, settings, registry)


def show_status(data):
    dashboard = data / 'dashboard'
    for label, name, entries in (
        ('Agents', 'agents.json', lambda snapshot: ((agent['name'], agent['status']) for agent in snapshot['agents'])),
        ('Integrations', 'integrations.json', lambda snapshot: snapshot['services'].items()),
    ):
        try:
            snapshot = load(dashboard / name)
        except FileNotFoundError:
            print(f'{label} (no status snapshot available)')
            continue
        timestamp = snapshot['time']
        print(snapshot_heading(label, timestamp))
        for entry, state in entries(snapshot):
            print(f'  {entry}  {state}')


def snapshot_heading(label, timestamp):
    updated = datetime.fromtimestamp(timestamp, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    age = 'stale, last updated' if time.time() - timestamp > 180 else 'updated'
    return f'{label} ({age} {updated})'


def show_telegram_status(data):
    dashboard = data / 'dashboard'
    snapshot = dashboard / 'telegram.json'
    if snapshot.is_file():
        report = load(snapshot)
        stamp = datetime.fromisoformat(report['generatedAt'].replace('Z', '+00:00')).timestamp()
        print(snapshot_heading('Telegram', stamp))
        for service, state in report['services'].items():
            print(f"  {service}  {state.get('health') or state['state']}")
        print(f"  Pending drafts  {report['pendingDrafts']}")
        print(f"  Unconfirmed deliveries  {report['unconfirmedDeliveries']}")
        if report['paused']:
            print('  Draft delivery paused')
        return
    snapshot = dashboard / 'integrations.json'
    if snapshot.is_file():
        report = load(snapshot)
        services = {name: state for name, state in report['services'].items() if name.startswith('Telegram')}
        if services:
            print(snapshot_heading('Telegram', report['time']))
            for name, state in services.items():
                print(f'  {name}  {state}')
            return
    print('Telegram (no status snapshot available)')


def open_existing(data):
    settings = load(data / 'settings.json')
    registry = load(data / 'agents.json')
    base_environment(settings, data)
    return settings, registry


def configure_network(settings, data, previous=None):
    from serve import configure
    configure(settings, data, load(data / 'agents.json'), previous)


@contextmanager
def operator_lock(data):
    with (data / '.operator.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def tailscale_dns():
    try:
        result = run('tailscale', 'status', '--json', capture_output=True, text=True)
        status = json.loads(result.stdout) or {}
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None
    name = ((status.get('Self') or {}).get('DNSName') or '').rstrip('.')
    if status.get('BackendState') == 'Running' and name in (status.get('CertDomains') or []):
        return name
    return None


def interactive(presets, saved):
    dns = tailscale_dns()
    network = {'proxy': 'Use an existing private HTTPS proxy'}
    if dns:
        network = {'tailscale': f'Use Tailscale Serve ({dns})', **network}
    preferred = 'tailscale' if dns and saved.get('tailscale', True) else 'proxy'
    tailnet = choose_option('🌐 Choose private access for Matrix and the dashboard', network, preferred) == 'tailscale'
    server = dns if tailnet else saved.get('server_name')
    if not server:
        server = prompt_text('🌐 Private Matrix DNS name',
                             lambda value: None if SERVER.fullmatch(value) else 'Enter a DNS name such as matrix.example.')

    saved_owner = saved.get('owner', '')

    def username_error(value):
        if saved_owner and value != saved_owner:
            return 'Use the existing Matrix operator username for this workspace.'
        if not valid_operator_username(value, presets):
            return ('Use up to 64 lowercase letters, digits, periods, underscores, and hyphens. '
                    'Start with a letter or digit and choose a name distinct from the agents and services.')
        return None

    owner = prompt_text('👤 Matrix operator username', username_error, default=saved_owner)
    passphrase = password({'owner': owner}, confirm=True)

    initial_integrations = [name for name in ('telegram', 'dot') if saved.get(name, True)]
    integrations = select_integrations(initial_integrations)
    settings = {'agents': [], **saved, 'server_name': server, 'owner': owner,
                'tailscale': tailnet, 'dot': 'dot' in integrations, 'telegram': 'telegram' in integrations}
    return settings, passphrase


def workspace_command(data, *, elevated=True):
    target = '' if data == DATA else f' --data-dir {quote(str(data))}'
    return ('sudo ' if elevated else '') + f'./constellation_matrix{target}'


def show_element_setup(settings, data=DATA, *, elevated=True):
    from setup_guide import show_printable
    show_printable(settings, workspace_command(data, elevated=elevated))


def finalize_setup(data, *, print_only=False, passphrase=None):
    from setup_guide import interactive_terminal
    settings = load(data / 'settings.json')
    if print_only or not interactive_terminal():
        show_element_setup(settings, data)
        return
    from onboarding import guide
    guide(data, settings, workspace_command(data), passphrase)


def install(data, args, *, entered=None):
    from setup_guide import interactive_terminal
    registry_path = data / 'agents.json'
    saved_registry = load(registry_path) if registry_path.exists() else {}
    presets = available_profiles(saved_registry)
    previous = data / 'settings.json'
    registry_file = data / 'agents.json'
    if registry_file.exists() and 'root' in load(registry_file)['agents']:
        raise ValueError('Run agent migrate-root before updating the existing Root deployment.')
    if entered is not None:
        raw, passphrase = entered['settings'], entered['password']
        if not isinstance(passphrase, str) or password_error(passphrase):
            raise ValueError('Enter a valid Matrix operator password')
        allowed = ('server_name', 'owner', 'tailscale', 'telegram', 'dot')
        settings = normalized({'agents': [], **(load(previous) if previous.exists() else {}),
                               **{key: raw[key] for key in allowed}}, presets)
    elif args.config:
        settings = normalized(load(args.config), presets)
        if not settings.get('owner_password_file'):
            raise ValueError('Config-file installation requires a private owner_password_file')
        passphrase = password(settings)
    else:
        raw, passphrase = interactive(presets, load(previous) if previous.exists() else {})
        settings = normalized(raw, presets)
    directory(data, 0o711)
    with operator_lock(data):
        current = load(previous) if previous.exists() else None
        if current:
            for key in ('server_name', 'owner', 'project', 'matrix_port'):
                if current[key] != settings[key]:
                    raise ValueError(f'{key} belongs to the existing deployment identity')
        source = settings.pop('neo4j_password_file', None)
        if entered is None:
            prepare_graph_password(data, source, interactive=not args.config)
        else:
            value = entered.get('graph_password')
            if value is not None and not isinstance(value, str):
                raise ValueError('Enter a valid Context Intelligence password')
            save_graph_password(data, value)
        if settings['systemd']:
            from host_services import enable_host_dependencies
            enable_host_dependencies(settings)
        security = json.loads(command_output('docker', 'info', '--format', '{{json .SecurityOptions}}'))
        if any(option.split(',')[0] == 'name=rootless' for option in security or []):
            raise ValueError('Select a standard Docker Engine context')
        prepare_storage(settings, data)
        registry_path = data / 'agents.json'
        registry = load(registry_path) if registry_path.exists() else {
            'local_server': settings['server_name'], 'dynamic_handoffs': True, 'agents': {},
            'team_version': 1 if set(settings['agents']).intersection({'chief-of-staff', 'manager'}) else 2}
        if any(entry.get('retirement') for entry in registry['agents'].values()):
            raise ValueError('Resume the pending agent removal before reinstalling')
        if current and current.get('roster_management'):
            settings['roster_management'] = True
        else:
            settings.pop('roster_management', None)
        for key in settings['agents']:
            if key not in registry['agents']:
                register_entry(settings, registry, key, presets[key])
        settings.pop('owner_password_file', None)
        onboarding_path = data / 'onboarding.json'
        if not args.config and not current and not onboarding_path.exists():
            save(onboarding_path, {'confirmed': [], 'select_agents': True})
        save(previous, settings)
        commit_registry(registry_path, registry, actor=settings['owner'], reason='installation')
        environment = compose_config(settings, data, registry)
        compose(data, 'config', '--quiet')
        service_units(settings, data, broker=registry['agents'].get('vps-manager', {}).get('active', False))
        homeserver = data / 'synapse/homeserver.yaml'
        if not homeserver.exists():
            compose(data, 'run', '--rm', '--no-deps', 'synapse', 'generate')
            write_config(data, environment)
        if (data / 'context/data/identity/api-keys.json').exists():
            up(data, 'postgres', 'synapse')
            compose(data, 'build', 'context')
            refresh_context(data)
        else:
            up(data, 'postgres', 'synapse', 'context')
        office_setup = not args.config and onboarding_path.exists() and load(onboarding_path).get('select_agents') is True
        provision_layout(settings, data, registry, passphrase, unattended=bool(args.config) or entered is not None,
                         office_setup=office_setup)
        compose(data, 'build', 'monitor')
        prepare_images(settings, registry)
        local_agents = [key for key, entry in registry['agents'].items() if entry['placement'] == 'local']
        services = ['monitor', 'private-edge']
        if settings['dot']:
            services.append('dot')
        services.extend(f'engine-{key}' for key in local_agents)
        up(data, *services)
        if settings['telegram']:
            run_telegram(data, 'start-guide')
        sync_workshop(settings, data, registry)
        up(data, *(key for key in local_agents if registry['agents'][key]['active']))
        publish_inventory(settings, data, registry)
        configure_network(settings, data, current)
        if settings['systemd']:
            from host_services import enable_services
            enable_services(settings)
        if args.config and onboarding_path.exists():
            state = load(onboarding_path)
            if state.pop('select_agents', False):
                state.pop('selection', None)
                save(onboarding_path, state)
                close_office_setup_notice(data)
    refresh_connections(data)
    if any(registry['agents'].get(k, {}).get('active') for k in PRIVATE_ROLES | {'chief-of-staff'}):
        from roster import enable_management
        enable_management(data, automatic=True)
    print('Workspace deployed.')
    if entered is not None:
        return
    if args.config or not interactive_terminal():
        print(f'Continue setup from an interactive terminal.\n  {workspace_command(data, elevated=False)} setup-guide')
        return
    finalize_setup(data, passphrase=passphrase)


def provision_selected_agents(data, settings, registry, selection, passphrase, *, operator_token=None):
    from roster import prepare_management

    presets = available_profiles(registry)
    roster = normalized({**settings, 'agents': selection}, presets)
    for key in selection:
        if key not in registry['agents']:
            register_entry(roster, registry, key, presets[key])
    if selection:
        rooms = load(data / 'rooms.json')
        if operator_token is not None:
            verify_operator(rooms, operator_token)
        # Publish config and start engines as a batch. Agent registration and Compose are safe to retry.
        compose_config(roster, data, registry)
        refresh_context(data)
        if operator_token is None:
            token = login(roster['owner'], passphrase if passphrase is not None else password(roster))['access_token']
        else:
            token = operator_token
        try:
            for key in selection:
                prepare_agent_state(data, key, registry['agents'][key], rooms, token, roster)
            prepare_management(data, roster, registry, token)
        finally:
            if operator_token is None:
                request('POST', CLIENT + '/logout', {}, token)
        prepare_images(roster, registry)
        up(data, *(f'engine-{key}' for key in selection if registry['agents'][key]['placement'] == 'local'))
        sync_workshop(roster, data, registry)
        commit_registry(data / 'agents.json', registry, actor=roster['owner'], reason='onboarding registration')
        publish_inventory(roster, data, registry)
        configure_network(roster, data, settings)
    save(data / 'settings.json', roster)
    close_office_setup_notice(data)


def provision_agent(data, settings, registry, candidate, inviter, *, actor=None, approval=None):
    key = candidate['key']
    if any(entry.get('retirement') in {'stopped', 'revoked'} for entry in registry['agents'].values()):
        raise ValueError('Resume the pending agent removal before adding another worker')
    register_entry(settings, registry, key, candidate)
    compose_config(settings, data, registry)
    refresh_context(data)
    prepare_agent_state(data, key, registry['agents'][key], load(data / 'rooms.json'), inviter, settings)
    if key in PRIVATE_ROLES | {'chief-of-staff'}:
        from roster import prepare_management
        prepare_management(data, settings, registry, inviter)
    if registry['agents'][key]['placement'] == 'local':
        prepare_images(settings, registry)
        up(data, 'engine-' + key)
    commit_registry(data / 'agents.json', registry, actor=actor or settings.get('owner', 'operator'),
                    reason='registration', approval=approval)
    sync_workshop(settings, data, registry)
    publish_inventory(settings, data, registry)
    return key


def agent_add(data, args):
    candidate = load(args.profile)
    configure = getattr(args, 'configure', False)
    if configure:
        from roster import local_worker
        local_worker(candidate['key'], {'profile': candidate['profile'], 'placement': agent_placement(candidate['key'], candidate)})
        if not sys.stdin.isatty():
            raise ValueError('Run --configure from an interactive terminal')
    with operator_lock(data):
        settings, registry = open_existing(data)
        own = login(settings['owner'], password(settings, args.owner_password_file))['access_token']
        try:
            key = provision_agent(data, settings, registry, candidate, own)
        finally:
            request('POST', CLIENT + '/logout', {}, own)
    if configure:
        agent_activate(data, key, pending_only=True)
    else:
        print('Provisioned ' + key + '. Complete its provider sign-in to activate routing.')


def pending_local_agents(registry):
    return [key for key, entry in registry['agents'].items()
            if entry['placement'] == 'local' and not entry['active'] and not entry.get('retirement')]


def agent_sign_in(data, key=None, *, selection=None):
    if key is not None:
        agent_activate(data, key)
        return
    pending = pending_local_agents(load(data / 'agents.json'))
    if selection is not None:
        pending = [key for key in pending if key in selection]
    if not pending:
        print('All local agents are already connected.')
    for number, key in enumerate(pending, start=1):
        print(f'Configuring local agent {number} of {len(pending)}', flush=True)
        agent_activate(data, key, pending_only=True)


def configure_amplifier(data, engine, entry):
    print(f"Amplifier setup for {entry['name']}. Add or edit a provider, select a model, then choose Done.", flush=True)
    app = ('/opt/amplifier-cli/.venv/bin/python', '-B', '/opt/office/provider_setup.py')
    compose(data, '--profile', 'agents', 'exec', engine, *app)
    result = compose(data, '--profile', 'agents', 'exec', '-T', engine, *app, 'metadata',
                     stdout=subprocess.PIPE, text=True)
    configured = json.loads(result.stdout)
    # Re-render the restricted Office bundle from Amplifier's private settings.
    compose(data, '--profile', 'agents', 'restart', engine)
    up(data, engine)
    return configured


def agent_activate(data, key, *, pending_only=False):
    with operator_lock(data):
        settings, registry = open_existing(data)
        entry = registry['agents'][key]
        if entry.get('retirement'):
            raise ValueError(f'Agent {key} is retiring. Resume its removal first.')
        if pending_only and entry['active']:
            return
        if entry['placement'] != 'local':
            raise ValueError('Complete provider sign-in on the authorized worker host')
        engine = 'engine-' + key
        up(data, engine)
        if entry['active']:
            entry['active'] = False
            save(data / 'agents.json', registry)
            publish_inventory(settings, data, registry)
            compose(data, '--profile', 'agents', 'stop', key)
        configured = configure_amplifier(data, engine, entry)
        if key == 'vps-manager':
            state = data / 'state/vps-manager'
            for source, target in (('credential-token', 'engine-token'), ('ca.pem', 'engine-ca.pem')):
                path = state / 'gateway-credentials' / target
                save(path, (state / 'exchange' / source).read_text(), mode=0o440)
                os.chown(path, 0, ROOT_UID)
            service_units(settings, data, broker=True)
            compose(data, '--profile', 'agents', 'run', '--rm', '--no-deps', '-T', key, 'node', '/app/root/agent.mjs', 'probe')
        else:
            compose(data, '--profile', 'agents', 'run', '--rm', '--no-deps', '-T', key, 'node', '/app/probe.mjs')
        sync_workshop(settings, data, registry)
        up(data, key)
        entry['amplifier'] = configured
        entry['active'] = True
        save(data / 'agents.json', registry)
        publish_inventory(settings, data, registry)


def run_telegram(data, *args):
    env = {**os.environ, 'TELEGRAM_STACK_DIR': str(data), 'PYTHONPATH': str(SOURCE / 'deploy/matrix')}
    run('bash', str(SOURCE / 'integrations/telegram/telegram'), *args, env=env)


def provision_telegram_layout(settings, data, rooms, token, bridge_room=None, *, unattended=False):
    sys.path.insert(0, str(SOURCE / 'integrations/telegram'))
    try:
        from telegram_provision import classify_telegram_rooms, reconcile_layout
    finally:
        sys.path.pop(0)
    root = data / 'telegram'
    directory(root, 0o711)
    for name in ('connector', 'guide'):
        directory(root / name, uid=RUNTIME_UID)
    save(root / 'guide/command', workspace_command(data) + '\n', uid=RUNTIME_UID)
    active = '/data/telegram-appservice.yaml' in load(data / 'synapse/homeserver.yaml').get('app_service_config_files', [])

    def select(candidates):
        if bridge_room in candidates:
            return bridge_room
        if unattended:
            print(f'More than one Telegram Bridge DM matches. To finish the Telegram layout later, run:\n'
                  f'  {workspace_command(data, elevated=False)} telegram layout')
            return None
        if bridge_room or not sys.stdin.isatty():
            raise ValueError('Multiple private Telegram Bridge DMs match. Run telegram layout --bridge-room with one of: '
                             + ', '.join(candidates))
        return choose_option('✈️ Choose the Telegram Bridge chat to keep', {room: room for room in candidates})

    owner = rooms['owner']
    layout = reconcile_layout(root, settings['server_name'], owner, token, rooms['space'],
                              bridge_active=active, select=select)
    if active and (root / 'guide/space-db').is_file():
        portals = compose(data, 'exec', '-T', 'postgres', 'psql', '-qXAt', '--set', 'ON_ERROR_STOP=1',
                          '-U', 'synapse', '-d', 'matrix_telegram', '--set', f'owner={owner}',
                          input="SELECT room FROM telegram_space_sent_portal WHERE owner = :'owner';\n",
                          capture_output=True, text=True).stdout.splitlines()
        classify_telegram_rooms(layout['space'], owner, token, portals)
    return layout


def verify_operator(rooms, token):
    if request('GET', CLIENT + '/account/whoami', token=token).get('user_id') != rooms['owner']:
        raise ValueError('Sign in with the workspace’s Matrix operator account')


def telegram_layout_options(action, args):
    parser = argparse.ArgumentParser(prog=f'constellation_matrix telegram {action}', allow_abbrev=False)
    parser.add_argument('--owner-password-file', type=Path)
    parser.add_argument('--bridge-room', help='Room ID to use when multiple private bridge DMs match')
    return parser.parse_args(args)


def telegram(data, args, owner_password_file=None, *, operator_token=None):
    settings, registry = open_existing(data)
    if not settings['telegram']:
        raise ValueError('Select Telegram in the installer first')
    if not args:
        raise ValueError('Specify a Telegram command. See docs/telegram.md.')
    if args == ['status', '--refresh']:
        run_telegram(data, 'metrics')
        show_telegram_status(data)
        return
    if args == ['enable-operations']:
        from host_services import enable_telegram_units
        with operator_lock(data):
            enable_telegram_units(settings, data)
        return
    if args[0] in {'layout', 'activate'}:
        action = args[0]
        options = telegram_layout_options(action, args[1:])
        with operator_lock(data):
            workspace = load(data / 'rooms.json')
            if operator_token is None:
                token = login(settings['owner'], password(settings, options.owner_password_file or owner_password_file))['access_token']
            else:
                verify_operator(workspace, operator_token)
                token = operator_token
            try:
                if action == 'activate':
                    run_telegram(data, action)
                rooms = provision_telegram_layout(settings, data, workspace, token, options.bridge_room)
                guide_args = ('--refresh-bridge',) if action == 'layout' else ()
                run_telegram(data, 'start-guide', *guide_args)
                coordinator = telegram_office_agent(registry)
                if registry['agents'].get(coordinator, {}).get('active') and 'root' not in registry['agents']:
                    compose_config(settings, data, registry)
                    docker_build(CONNECTOR, SOURCE / 'agents/connector')
                    up(data, coordinator)
            finally:
                if operator_token is None:
                    request('POST', CLIENT + '/logout', {}, token)
        print('In Element, open Telegram → Telegram Control → Pinned messages.')
        if rooms['bridge']:
            print('Telegram Bridge is linked under Telegram.')
        return
    run_telegram(data, *args)
    if args[0] == 'prepare-agent':
        sync_workshop(settings, data, registry)
    if args[0] == 'dot-control' and settings['dot']:
        compose(data, 'restart', 'dot')


def sync_workshop(settings, data, registry, **options):
    from persona_management import synchronize
    return synchronize(data, registry, telegram=settings['telegram'], **options)


def persona_draft(data, role, observation):
    with operator_lock(data):
        settings, registry = open_existing(data)
        if not registry['agents'].get('chief-of-staff', {}).get('active'):
            raise ValueError('Activate Chief of Staff first')
        if not KEY.fullmatch(role) or not re.fullmatch(r'cos-(?:(?:direct|gap)-)?[a-f0-9]{64}', observation):
            raise ValueError('Pass an agent key and an archived observation ID')
        managed = (sync_workshop(settings, data, registry) or {}).get(role)
        if not observation.startswith('cos-gap-') and not managed:
            raise ValueError('Connect the agent in Persona Workshop before drafting a change')
        destination = data / 'state/chief-of-staff/persona-exports'
        directory(destination)
        export = destination / (role + '-' + observation)
        if export.exists():
            print('Persona draft already exported to ' + str(export))
            return
        with TemporaryDirectory(dir=data / 'staging') as name:
            work = Path(name)
            os.chown(work, RUNTIME_UID, RUNTIME_UID)
            compose(data, '--profile', 'agents', 'run', '--rm', '--no-deps', '-T', '-v',
                    f'{work}:/run-data', 'chief-of-staff', 'node',
                    '/app/chief-of-staff/persona/draft.mjs', role, observation)
            container = compose(data, '--profile', 'agents', 'ps', '-q', 'persona-workshop',
                                capture_output=True, text=True).stdout.strip()
            if not container:
                raise ValueError('Persona Workshop is unavailable')
            run(sys.executable, str(SOURCE / 'integrations/chief-of-staff/persona/compile-persona.py'),
                container, role, str(work), str(export), json.dumps(managed))


def service_units(settings, data, broker=False):
    if not settings['systemd']:
        if broker:
            raise ValueError('Root execution requires an operator-managed systemd socket')
        return
    from host_services import units
    units(settings, data, broker=broker)


def main():
    from operator_cli import (HOST_ACTIONS, configure_as_operator, execute_host, install_as_operator, print_guide,
                              publish_metadata, setup_as_operator, telegram_as_operator)
    from setup_guide import interactive_terminal

    parser = argparse.ArgumentParser(prog='constellation_matrix', description=__doc__, allow_abbrev=False)
    parser.add_argument('--data-dir', type=Path, default=DATA)
    commands = parser.add_subparsers(dest='command', required=True)
    install_parser = commands.add_parser('install')
    install_parser.add_argument('--config', type=Path)
    commands.add_parser('validate').add_argument('--config', type=Path, required=True)
    agent = commands.add_parser('agent').add_subparsers(dest='action', required=True)
    add = agent.add_parser('add')
    add.add_argument('--profile', type=Path, required=True)
    add.add_argument('--owner-password-file', type=Path)
    add.add_argument('--configure', action='store_true', help='Open Amplifier setup after registering a local Office worker')
    remove = agent.add_parser('remove', help='Drain, deregister and archive a local Office worker')
    remove.add_argument('key')
    remove.add_argument('--yes', action='store_true')
    management = agent.add_parser('management').add_subparsers(dest='management', required=True)
    enable = management.add_parser('enable', help='Enable approved roster changes in the Chief of Staff DM')
    enable.add_argument('--owner-password-file', type=Path)
    team = agent.add_parser('migrate-team', help='Preview or apply the five-agent starter team')
    team.add_argument('--apply', action='store_true')
    team.add_argument('--retire', action='append', default=[], metavar='AGENT')
    team.add_argument('--owner-password-file', type=Path)
    migrate = agent.add_parser('migrate-root', help='Replace legacy Root and preserve the Root Operations room')
    migrate.add_argument('--owner-password-file', type=Path)
    migrate.add_argument('--yes', action='store_true', help='Confirm retirement of the legacy Root account')
    agent.add_parser('configure', aliases=['sign-in'], help='Open native Amplifier provider setup').add_argument(
        'key', nargs='?', help='Agent ID. Omit to configure all pending local agents in order.')
    remote = commands.add_parser('remote').add_subparsers(dest='action', required=True)
    remote.add_parser('desktop').add_argument('--public-key', type=Path, required=True)
    export = remote.add_parser('export')
    export.add_argument('key')
    export.add_argument('--host', required=True)
    export.add_argument('--output', type=Path, required=True)
    attach = remote.add_parser('attach')
    attach.add_argument('--enrollment', type=Path, required=True)
    attach.add_argument('--desktop-mount', type=Path, required=True)
    attach.add_argument('--workshop-host', help='Private HTTPS name of this remote host')
    confirm = remote.add_parser('confirm')
    confirm.add_argument('key')
    confirm.add_argument('--receipt', type=Path, required=True)
    status = commands.add_parser('status', help='Read the dashboard snapshots')
    status.add_argument('--refresh', action='store_true', help='Publish dashboard files and refresh Docker inventory')
    setup = commands.add_parser('setup-guide', help='Resume workspace onboarding')
    setup.add_argument('--print', dest='print_only', action='store_true', help='Print Element sign-in and guide locations')
    removal = commands.add_parser('uninstall', help='Remove workspace services, with optional data deletion')
    removal.add_argument('--data-dir', type=Path, default=argparse.SUPPRESS,
                         help='Deployment data directory, defaults to /srv/constellation-matrix.')
    removal.add_argument('--purge', action='store_true',
                         help='Permanently delete the deployment data, backups, and project volumes.')
    commands.add_parser('backup')
    commands.add_parser('restore-check')
    commands.add_parser('telegram').add_argument('arguments', nargs=argparse.REMAINDER)
    commands.add_parser('dot-connect').add_argument('tunnel_id')
    commands.add_parser('chief-review')
    commands.add_parser('persona-sync')
    connect = commands.add_parser('persona-connect')
    connect.add_argument('agent')
    connect.add_argument('--url', required=True)
    connect.add_argument('--token-file', type=Path, required=True)
    connect.add_argument('--ca-file', type=Path)
    persona = commands.add_parser('persona-draft')
    persona.add_argument('role')
    persona.add_argument('observation')
    host = commands.add_parser('_host')
    host.add_argument('action', choices=HOST_ACTIONS)
    host.add_argument('key', nargs='?')
    host.add_argument('--pending-only', action='store_true')
    args = parser.parse_args()
    if args.command == 'validate':
        normalized(load(args.config), available_profiles())
        print('Deployment configuration is valid.')
        return
    data = args.data_dir.absolute()
    if args.command == 'status' and not args.refresh:
        show_status(data)
        return
    if args.command == 'telegram' and args.arguments == ['status']:
        show_telegram_status(data)
        return
    if args.command == 'setup-guide' and (args.print_only or not interactive_terminal()):
        print_guide(data, root=os.geteuid() == 0)
        return
    if os.geteuid():
        if args.command == 'install':
            install_as_operator(data, args)
            return
        if args.command == 'setup-guide':
            setup_as_operator(data)
            return
        if args.command == 'agent' and args.action in {'configure', 'sign-in'}:
            configure_as_operator(data, args.key)
            return
        if args.command == 'telegram' and args.arguments[:1] in (['activate'], ['layout']):
            telegram_as_operator(data, args.arguments)
            return
        parser.error('Run this deployment command with sudo')
    if data.is_symlink() or (data / 'settings.json').is_symlink() or data.resolve() == Path('/'):
        parser.error('Use a dedicated deployment directory')
    data = data.resolve()
    if (data / 'settings.json').exists() and load(data / 'settings.json').get('rootless'):
        parser.error('Use a separate directory for standard Docker. This directory contains an older rootless deployment')
    if args.command == '_host':
        execute_host(data, args)
        return
    if args.command == 'install':
        install(data, args)
    elif args.command == 'setup-guide':
        finalize_setup(data, print_only=args.print_only)
    elif args.command == 'uninstall':
        from uninstall import uninstall
        uninstall(data, purge=args.purge)
    elif args.command == 'agent':
        if args.action == 'add':
            agent_add(data, args)
        elif args.action == 'remove':
            from roster import remove_command
            remove_command(data, args.key, args.yes)
        elif args.action == 'management':
            from roster import enable_management
            enable_management(data, args.owner_password_file)
        elif args.action == 'migrate-team':
            from team_migration import migrate
            migrate(data, args)
            return
        elif args.action == 'migrate-root':
            from vps_migration import migrate_root
            migrate_root(data, args.owner_password_file, args.yes)
        else:
            agent_sign_in(data, args.key)
            if args.key in (None, 'chief-of-staff', *PRIVATE_ROLES):
                from roster import enable_management
                enable_management(data, automatic=True)
            selected = load(data / 'settings.json')['agents']
            if not set(selected).intersection(pending_local_agents(load(data / 'agents.json'))):
                finalize_setup(data)
    elif args.command == 'remote':
        from remote import execute
        execute(data, args)
        if args.action == 'confirm':
            from onboarding import deliver_welcome
            deliver_welcome(data, workspace_command(data))
    elif args.command in {'backup', 'restore-check'}:
        with operator_lock(data):
            open_existing(data)
            from recovery import backup
            backup(data, args.command)
    elif args.command == 'telegram':
        telegram(data, args.arguments)
    elif args.command in {'persona-sync', 'persona-connect', 'chief-review'}:
        with operator_lock(data):
            settings, registry = open_existing(data)
            if args.command == 'persona-connect':
                from persona_management import remember_remote
                path = data / 'workshop-management' / f'{args.agent}.json'
                previous = path.read_text() if args.agent in registry['agents'] and path.exists() else None
                remember_remote(data, registry, args.agent, args.url, args.token_file, args.ca_file)
                try:
                    sync_workshop(settings, data, registry, reconnect=args.agent)
                except Exception:
                    if previous is None:
                        path.unlink(missing_ok=True)
                    else:
                        save(path, previous)
                    raise
            if args.command == 'persona-sync':
                compose_config(settings, data, registry)
                up(data, *(f'engine-{key}' for key, entry in registry['agents'].items() if entry['placement'] == 'local'))
            if args.command != 'persona-connect':
                sync_workshop(settings, data, registry)
        if args.command == 'chief-review':
            compose(data, '--profile', 'agents', 'exec', '-T', 'chief-of-staff', 'node',
                    '/app/chief-of-staff/direct/run-review.mjs')
    elif args.command == 'persona-draft':
        persona_draft(data, args.role, args.observation)
    elif args.command == 'dot-connect':
        from dot import connect
        with operator_lock(data):
            settings, _ = open_existing(data)
            connect(settings, data, args.tunnel_id)
    elif args.command == 'status':
        with operator_lock(data):
            settings, registry = open_existing(data)
            if 'connection_history' not in registry:
                commit_registry(data / 'agents.json', registry)
            publish_dashboard(settings, data)
        publish_inventory(settings, data, registry)
        refresh_connections(data)
        compose(data, '--profile', 'agents', 'ps')
    if args.command in {'install', 'setup-guide', 'agent'} or args.command == 'remote' and args.action == 'confirm':
        publish_metadata(data)


if __name__ == '__main__':
    os.umask(0o077)
    try:
        main()
    except (KeyError, ValueError, OSError, MatrixError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'Constellation setup failed: {error}') from None
