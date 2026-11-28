"""Host lifecycle for local Office workers."""
from contextlib import closing, nullcontext
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
from connection_history import commit_registry
from provision import MatrixError

INVITER = 'roster-inviter'
TEAM_MANAGERS = {'hiring-manager', 'coach', 'scouter'}
PROTECTED = RESERVED | TEAM_MANAGERS | {'task-manager'} | {'manager', 'chief-of-staff', 'root', 'the-architect', 'remote-worker'}


def local_worker(key, entry):
    if (not isinstance(key, str) or not KEY.fullmatch(key) or len(key) > 36 or key in PROTECTED
            or not entry or entry.get('placement') != 'local' or entry.get('profile', {}).get('role') != 'worker'):
        raise ValueError('Choose an ordinary local Office worker')


def drain_queue(path, *, team=False):
    if not path.is_file():
        return None
    # Install the fence in the same database transaction as the queue check.
    # Older deployed connectors also enqueue through this table.
    if path.is_symlink():
        raise ValueError('Expected a regular connector database')
    with closing(sqlite3.connect(f'{path.as_uri()}?mode=rw', uri=True, timeout=3)) as db:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if team:
                db.execute('''CREATE TRIGGER IF NOT EXISTS team_retirement BEFORE INSERT ON inbox
                           WHEN NEW.number <= 1 OR NOT EXISTS (SELECT 1 FROM inbox WHERE request=NEW.request)
                           BEGIN SELECT RAISE(IGNORE); END''')
                waiting = db.execute('''SELECT 1 FROM inbox AS start WHERE start.id=start.request AND
                    json_extract(start.outgoing,'$.target') IS NOT NULL AND NOT EXISTS
                    (SELECT 1 FROM inbox AS final WHERE final.request=start.request AND
                     json_extract(final.outgoing,'$.content."net.matrix_agents.task_outcome".status') IS NOT NULL) LIMIT 1''').fetchone()
                if waiting:
                    return True
            else:
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
    if key == 'the-architect':
        for relative in ('matrix/gateway.sqlite3', 'matrix/gateway.sqlite3-wal', 'matrix/gateway.sqlite3-shm', 'execution'):
            source = state / relative
            if source.exists():
                source.rename(archive / source.name)
    if state.exists():
        rmtree(state)
    (data / 'context' / f'{key}.token').unlink(missing_ok=True)


def desk_pending(path):
    if not path.is_file():
        return False
    with closing(sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True)) as db:
        tables = {name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return 'roster_requests' in tables and bool(db.execute('SELECT 1 FROM roster_requests WHERE done=0 LIMIT 1').fetchone())


def drain_host(path):
    if not path.is_file():
        return None
    with closing(sqlite3.connect(f'{path.as_uri()}?mode=rw', uri=True, timeout=3)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("CREATE TRIGGER IF NOT EXISTS team_retirement BEFORE INSERT ON tasks BEGIN SELECT RAISE(IGNORE); END")
        return bool(db.execute('SELECT 1 FROM tasks WHERE outcome IS NULL OR notified=0 OR '
                               '(source_thread IS NOT NULL AND source_notified=0) LIMIT 1').fetchone())


def retire_locked(data, settings, registry, key, *, actor=None, approval=None):
    from persona_management import disconnect

    retired = registry.get('retired', {})
    if key in retired:
        publish_inventory(settings, data, registry)
        return 'retired'
    entries = registry['agents']
    entry = entries.get(key)
    if entry and entry.get('mobility_move'):
        raise ValueError('Finish or roll back the agent’s move before retiring it')
    if entry and entry.get('mobility_generation') and entry['placement'] == 'remote':
        raise ValueError('Move this agent back to the VPS before retiring it')
    starter = bool(entry and registry.get('team_version') == 2 and entry.get('starter_role'))
    if not starter:
        local_worker(key, entry)
    progress = entry.get('retirement')
    if not progress:
        progress = entry['retirement'] = 'draining'
        entry['retirement_actor'] = actor or settings.get('owner', 'operator')
        if approval:
            entry['retirement_approval'] = approval
        commit_registry(data / 'agents.json', registry, actor=entry['retirement_actor'],
                        reason='retirement started', approval=approval)
    if progress == 'draining':
        matrix = data / 'state' / key / 'matrix'
        if key == 'the-architect':
            pending = drain_host(matrix / 'gateway.sqlite3')
        elif registry.get('team_version') == 2:
            pending = desk_pending(matrix / 'connector.sqlite3') or drain_queue(matrix / 'connector.sqlite3', team=True)
        else:
            pending = drain_queue(matrix / 'connector.sqlite3')
        publish_inventory(settings, data, registry)
        another_stopping = any(other != key and saved.get('retirement') in {'stopped', 'revoked'}
                               for other, saved in entries.items())
        if pending or (pending is None and entry['active']) or another_stopping:
            return 'draining'
        services = [key, 'engine-' + key] + (['helper-dm'] if key == 'helper' else [])
        compose(data, '--profile', 'agents', 'stop', *services)
        compose(data, '--profile', 'agents', 'rm', '-f', *services)
        if key == 'the-architect' and settings['systemd']:
            from host_services import remove_host_broker
            remove_host_broker(settings)
        progress = entry['retirement'] = 'stopped'
        save(data / 'agents.json', registry)
    if progress == 'stopped':
        deactivate_worker(data, key, settings)
        disconnect(data, key)
        if len(entries) == 1 and not settings['telegram'] and 'persona-workshop' in load(data / 'compose.yaml')['services']:
            compose(data, '--profile', 'agents', 'rm', '-sf', 'persona-workshop')
        if entry.get('hiring_provider'):
            # The gateway rereads every per-agent grant. Revocation stays durable across restarts.
            (data / 'hiring' / 'grants' / (key + '.json')).unlink(missing_ok=True)
            (data / 'state' / key / 'hiring-agent/token').unlink(missing_ok=True)
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
    if key in TEAM_MANAGERS:
        (data / 'roster' / (key + '.key')).unlink(missing_ok=True)
    if key == 'the-architect':
        (data / 'context/the-architect-ops.token').unlink(missing_ok=True)
    archive_worker(data, key)
    registry.setdefault('retired', {})[key] = {'name': entry['name'], 'at': datetime.now(timezone.utc).isoformat()}
    save(data / 'settings.json', settings)
    commit_registry(data / 'agents.json', registry, actor=entry.get('retirement_actor', settings.get('owner', 'operator')),
                    reason='retirement completed', approval=entry.get('retirement_approval'))
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


def invitation_ready(data):
    auth = data / 'roster'
    ready = auth / 'invitation-ready'
    return (ready.is_file() and (auth / 'inviter-token').is_file()
            and ready.read_text().strip() == load(data / 'rooms.json')['office'])


def prepare_invitation(data, settings, operator):
    auth = data / 'roster'
    directory(auth)
    office_inviter(data, settings, operator)
    save(auth / 'invitation-ready', load(data / 'rooms.json')['office'] + '\n')


def prepare_management(data, settings, registry, operator):
    if ((settings['systemd'] or settings.get('control_runtime'))
            and {'hiring-manager', 'chief-of-staff'}.intersection(registry['agents']) and not invitation_ready(data)):
        prepare_invitation(data, settings, operator)


def enable_management(data, password_file=None, *, automatic=False, locked=False):
    from host_services import roster_units
    from installer import configure_network, prepare_images

    with nullcontext() if locked else operator_lock(data):
        settings = load(data / 'settings.json')
        registry = load(data / 'agents.json')
        authorities = [key for key in ('chief-of-staff', 'hiring-manager', 'coach', 'scouter')
                       if registry['agents'].get(key, {}).get('active') and not registry['agents'][key].get('retirement')
                       and (key == 'chief-of-staff' or registry['agents'][key].get('starter_role') == key)]
        if 'hiring-manager' in registry['agents'] and 'chief-of-staff' in authorities:
            authorities.remove('chief-of-staff')
        managed_hiring = 'hiring-manager' in registry['agents']
        needs_hiring_upgrade = managed_hiring and not (data / 'roster/element-hiring-v1').is_file()
        if automatic and not needs_hiring_upgrade and settings.get('roster_management') and all(
                (data / 'roster' / (key + '.key')).exists() for key in authorities if key != 'chief-of-staff'):
            return
        if not (settings['systemd'] or settings.get('control_runtime')) or not authorities:
            if automatic:
                return
            raise ValueError('Enable systemd and activate Chief of Staff before enabling roster management')
        settings, registry = open_existing(data)
        auth = data / 'roster'
        directory(auth)
        if {'hiring-manager', 'chief-of-staff'}.intersection(authorities) and (not automatic or not invitation_ready(data)):
            operator = login(settings['owner'], password(settings, password_file))['access_token']
            try:
                prepare_invitation(data, settings, operator)
            finally:
                request('POST', CLIENT + '/logout', {}, operator)
        for key in authorities:
            private_secret(auth / ('bridge-key' if key == 'chief-of-staff' else key + '.key'), uid=RUNTIME_UID)
        if needs_hiring_upgrade:
            prepare_images(settings, registry)
        if settings.get('control_runtime'):
            up(data, 'control-runtime')
        else:
            roster_units(settings, data)
        settings['roster_management'] = True
        compose_config(settings, data, registry)
        up(data, *authorities)
        if managed_hiring:
            configure_network(settings, data)
            save(auth / 'element-hiring-v1', 'enabled\n')
        save(data / 'settings.json', settings)
        publish_inventory(settings, data, registry)
    print('Team management is ready in Element.')


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
