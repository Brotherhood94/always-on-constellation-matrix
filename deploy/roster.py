"""Host lifecycle for local Office workers."""
from contextlib import closing
from datetime import datetime, timezone
import sqlite3
from pathlib import Path
from shutil import rmtree
from subprocess import SubprocessError
import sys
import time

from installer import (CLIENT, KEY, RESERVED, RUNTIME_UID, SOURCE, command_output, compose, compose_config, directory, load,
                       login, open_existing, operator_lock, password, private_secret, publish_inventory,
                       read_password_file, refresh_context, request, run, save, sync_workshop, up)
from layout import invite, join, room_route
from provision import MatrixError

INVITER = 'roster-inviter'
PROTECTED = RESERVED | {'manager', 'chief-of-staff', 'root', 'remote-worker'}


def local_worker(key, entry):
    if (not isinstance(key, str) or not KEY.fullmatch(key) or len(key) > 36 or key in PROTECTED
            or not entry or entry.get('placement') != 'local' or entry.get('profile', {}).get('role') != 'worker'):
        raise ValueError('Choose an ordinary local Office worker')


def drain_queue(path):
    if not path.is_file():
        return None
    # Install the fence in the same database transaction as the queue check.
    # Older deployed connectors also enqueue through this table.
    if path.is_symlink():
        raise ValueError('Expected a regular connector database')
    with closing(sqlite3.connect(f'{path.as_uri()}?mode=rw', uri=True, timeout=3)) as db:
        with db:
            db.execute('BEGIN IMMEDIATE')
            db.execute((SOURCE / 'deploy/retire-worker.sql').read_text())
            return db.execute('''
                SELECT EXISTS(SELECT 1 FROM inbox WHERE sent IS NULL) OR
                       EXISTS(SELECT 1 FROM assignment_reactions JOIN inbox ON assignment=inbox.id
                              WHERE inbox.sent IS NOT NULL AND
                              ((activity_id IS NOT NULL AND activity_removed=0) OR
                               (outcome_key IS NOT NULL AND outcome_id IS NULL))) OR
                       EXISTS(SELECT 1 FROM context_outbox)
            ''').fetchone()[0]


def deactivate_worker(data, key, settings):
    user = f"@{key}:{settings['server_name']}"
    state = data / 'state' / key
    receipt = state / 'matrix-retired'
    if receipt.is_file():
        return
    secret = read_password_file(state / 'matrix' / 'matrix-password', 'Worker Matrix')
    identity = {'type': 'm.id.user', 'user': user}
    auth = {'type': 'm.login.password', 'identifier': identity, 'password': secret}
    try:
        session = request('POST', CLIENT + '/login', auth)
    except MatrixError as error:
        if error.code == 'M_USER_DEACTIVATED' or (error.code == 'M_FORBIDDEN' and account_deactivated(data, user)):
            save(receipt, user + '\n')
            return
        raise
    token = session['access_token']
    if session.get('user_id') != user:
        request('POST', CLIENT + '/logout', {}, token)
        raise ValueError('Matrix returned a different worker identity')
    try:
        request('POST', CLIENT + '/account/deactivate', {'auth': auth, 'erase': False}, token)
    except (OSError, MatrixError):
        try:
            request('POST', CLIENT + '/logout', {}, token)
        except (OSError, MatrixError):
            pass
        raise
    save(receipt, user + '\n')


def account_deactivated(data, user):
    # Synapse may return the same login error for a deactivated user and invalid credentials.
    result = compose(data, 'exec', '-T', 'postgres', 'psql', '-XAt', '-U', 'synapse', '-d', 'synapse',
                     '-v', 'ON_ERROR_STOP=1', '-v', f'worker={user}',
                     input="SELECT deactivated FROM users WHERE name = :'worker';\n", capture_output=True, text=True)
    return result.stdout.strip() == '1'


def archive_worker(data, key):
    from uninstall import check_purge_mounts

    state = data / 'state' / key
    archive = data / 'archive' / 'agents' / key
    directory(data / 'archive')
    directory(archive.parent)
    directory(archive)
    if state.is_symlink():
        raise ValueError('Expected a plain worker state directory')
    check_purge_mounts(state)
    for relative, label in (('work', 'workspace'), ('instance/sessions', 'instance-sessions'),
                            ('instance/backend/acp/sessions', 'amplifier-sessions')):
        source = state / relative
        if any(path.is_symlink() for path in (source, *source.parents) if path.is_relative_to(state)):
            raise ValueError('Expected plain worker archive directories')
        if source.is_dir():
            source.rename(archive / label)
    # The Matrix session store and the provider home hold credentials.
    database = state / 'matrix/connector.sqlite3'
    if not database.parent.is_symlink() and database.is_file() and not database.is_symlink():
        for suffix in ('', '-wal', '-shm'):
            part = database.with_name(database.name + suffix)
            if part.is_file() and not part.is_symlink():
                part.rename(archive / part.name)
    if state.exists():
        rmtree(state)
    (data / 'context' / f'{key}.token').unlink(missing_ok=True)


def retire_locked(data, settings, registry, key):
    from persona_management import disconnect

    retired = registry.get('retired', {})
    if key in retired:
        publish_inventory(settings, data, registry)
        return 'retired'
    entries = registry['agents']
    entry = entries.get(key)
    local_worker(key, entry)
    progress = entry.get('retirement')
    if not progress:
        progress = entry['retirement'] = 'draining'
        save(data / 'agents.json', registry)
    if progress == 'draining':
        pending = drain_queue(data / 'state' / key / 'matrix/connector.sqlite3')
        publish_inventory(settings, data, registry)
        another_stopping = any(other != key and saved.get('retirement') in {'stopped', 'revoked'}
                               for other, saved in entries.items())
        if pending or (pending is None and entry['active']) or another_stopping:
            return 'draining'
        compose(data, '--profile', 'agents', 'stop', key, 'engine-' + key)
        compose(data, '--profile', 'agents', 'rm', '-f', key, 'engine-' + key)
        progress = entry['retirement'] = 'stopped'
        save(data / 'agents.json', registry)
    if progress == 'stopped':
        deactivate_worker(data, key, settings)
        disconnect(data, key)
        if len(entries) == 1 and not settings['telegram'] and 'persona-workshop' in load(data / 'compose.yaml')['services']:
            compose(data, '--profile', 'agents', 'rm', '-sf', 'persona-workshop')
        progress = entry['retirement'] = 'revoked'
        save(data / 'agents.json', registry)
    # On retries the live entry remains fenced until all credential and data steps finish.
    del entries[key]
    settings['agents'] = [agent for agent in settings['agents'] if agent != key]
    settings.get('subscriptions', {}).get('overrides', {}).pop(key, None)
    compose_config(settings, data, registry)
    refresh_context(data, retire=key)
    sync_workshop(settings, data, registry)
    configured = load(data / 'compose.yaml')['networks']
    for network in (f'acp-{key}', 'provider', 'workshop-targets'):
        if network not in configured:
            networks = command_output('docker', 'network', 'ls', '-q', '--filter',
                                      f"label=com.docker.compose.project={settings['project']}", '--filter',
                                      f'label=com.docker.compose.network={network}').split()
            if networks:
                run('docker', 'network', 'rm', *networks)
    archive_worker(data, key)
    registry.setdefault('retired', {})[key] = {'name': entry['name'], 'at': datetime.now(timezone.utc).isoformat()}
    save(data / 'settings.json', settings)
    save(data / 'agents.json', registry)
    publish_inventory(settings, data, registry)
    return 'retired'


def remove_command(data, key, confirmed):
    if not confirmed:
        if not sys.stdin.isatty():
            raise ValueError('Use --yes to confirm removal in a script')
        if input(f'Type {key} to retire and archive this worker: ').strip() != key:
            print('Agent removal cancelled.')
            return
    deadline = time.monotonic() + 45
    while True:
        with operator_lock(data):
            settings, registry = open_existing(data)
            result = retire_locked(data, settings, registry, key)
        if result == 'retired':
            print(f'Retired {key}. Archive: {data / "archive" / "agents" / key}')
            return
        if time.monotonic() >= deadline:
            print(f'{key} is still draining. Check the worker and rerun agent remove {key} to resume.')
            return
        time.sleep(3)


def office_inviter(data, settings, operator):
    room = load(data / 'rooms.json')['office']
    owner = f"@{settings['owner']}:{settings['server_name']}"
    user = f"@{INVITER}:{settings['server_name']}"
    route = room_route(room) + '/state/m.room.power_levels'
    powers = request('GET', route, token=operator)
    users = powers.get('users', {})
    baseline = [powers.get('state_default', 50), powers.get('redact', 50), powers.get('kick', 50),
                powers.get('ban', 50), *powers.get('events', {}).values()]
    if (powers.get('users_default', 0) >= 1 or min(baseline) <= 1 or
            any(level >= 1 and key not in {owner, user} for key, level in users.items())):
        raise ValueError('Office has custom power levels. Give only the owner room-management permissions before enabling the roster service.')
    auth = data / 'roster'
    secret = private_secret(auth / 'inviter-password')
    token_path = auth / 'inviter-token'
    if not token_path.exists():
        identity = login(INVITER, secret, 'CONSTELLATIONROSTER', 'Office roster invitation service')
        if identity['user_id'] != user:
            raise ValueError('Matrix returned a different roster service identity')
        save(token_path, identity['access_token'] + '\n')
    token = read_password_file(token_path, 'Office invitation')
    invite(room, user, operator)
    join(room, user, token)
    if powers.get('invite') != 1 or users.get(user) != 1:
        request('PUT', route, {**powers, 'invite': 1, 'users': {**users, user: 1}}, operator)
    return token


def enable_management(data, password_file):
    from host_services import roster_units

    with operator_lock(data):
        settings, registry = open_existing(data)
        if not settings['systemd'] or not registry['agents'].get('chief-of-staff', {}).get('active'):
            raise ValueError('Enable systemd and activate Chief of Staff before enabling roster management')
        auth = data / 'roster'
        directory(auth)
        operator = login(settings['owner'], password(settings, password_file))['access_token']
        try:
            office_inviter(data, settings, operator)
        finally:
            request('POST', CLIENT + '/logout', {}, operator)
        private_secret(auth / 'bridge-key', uid=RUNTIME_UID)
        roster_units(settings, data)
        settings['roster_management'] = True
        compose_config(settings, data, registry)
        up(data, 'chief-of-staff')
        save(data / 'settings.json', settings)
        publish_inventory(settings, data, registry)
    print('Roster management is ready in the Chief of Staff DM.')


def reconcile_retirements(data):
    with operator_lock(data):
        settings, registry = open_existing(data)
        for key in tuple(registry['agents']):
            if registry['agents'][key].get('retirement'):
                # Reload between operations after any partial write.
                settings, registry = open_existing(data)
                try:
                    retire_locked(data, settings, registry, key)
                except (ValueError, MatrixError, OSError, SubprocessError, sqlite3.Error) as error:
                    print(f'Retirement for {key} will retry ({type(error).__name__}).', flush=True)
