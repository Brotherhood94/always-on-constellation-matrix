"""Replace the legacy Root agent without reusing its approval authority."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys

from installer import (CLIENT, PRESETS, compose, compose_config, directory, load, login, open_existing,
                       operator_lock, password, prepare_agent_state, prepare_images, publish_inventory,
                       refresh_context, register_entry, request, run, save, sync_workshop, up)
from layout import encoded, membership, missing, room_route
from connection_history import commit_registry
from persona_management import disconnect
from roster import archive_worker, deactivate_worker


def stop_legacy(data, settings):
    services = load(data / 'compose.yaml')['services']
    if 'root' in services:
        compose(data, '--profile', 'agents', 'stop', 'root', 'engine-root')
    service = settings['project'] + '-root'
    unit = Path('/etc/systemd/system')
    if (unit / (service + '.socket')).is_file():
        run('systemctl', 'disable', '--now', service + '.socket')
    history = data / 'state/root/execution/execution.sqlite3'
    if history.is_file():
        with closing(sqlite3.connect(history.as_uri() + '?mode=rw', uri=True, timeout=3)) as db, db:
            running = db.execute("SELECT 1 FROM audit WHERE outcome='running' LIMIT 1").fetchone()
            if running:
                active = run('systemctl', 'is-active', service + '.service', capture_output=True,
                             text=True, check=False).stdout.strip()
                if active in {'active', 'activating', 'deactivating'}:
                    raise ValueError('A Root operation is still running. Wait for it to finish, then resume agent migrate-root.')
                db.execute("UPDATE audit SET outcome='unknown' WHERE outcome='running'")
                print('Review the legacy audit archive for an unconfirmed operation.')
    if (unit / (service + '.service')).is_file():
        run('systemctl', 'stop', service + '.service')
    if history.is_file():
        with closing(sqlite3.connect(history.as_uri() + '?mode=rw', uri=True)) as db, db:
            db.execute("UPDATE audit SET outcome='unknown' WHERE outcome='running'")
    if 'root' in services:
        compose(data, '--profile', 'agents', 'rm', '-f', 'root', 'engine-root')


def revoke_legacy(data, settings, rooms, operator):
    user = f"@root:{settings['server_name']}"
    audit = (data / 'state/root/matrix/room-id').read_text().strip()
    if not audit or audit == rooms['office']:
        raise ValueError('Could not identify the legacy Control Room chat')
    alias = encoded(f"#root-dm:{settings['server_name']}")
    existing = missing(CLIENT + '/directory/room/' + alias, operator)
    if (existing or {}).get('room_id') != audit:
        raise ValueError('Restore the #root-dm alias to the original Control Room room before migrating')
    for room in (audit, rooms['office']):
        if membership(room, user, operator) in {'invite', 'join'}:
            request('POST', room_route(room) + '/kick', {'user_id': user, 'reason': 'Replaced by The Architect'}, operator)
    account_data = f"{CLIENT}/user/{encoded(rooms['owner'])}/account_data/m.direct"
    direct = missing(account_data, operator) or {}
    if audit in direct.get(user, []):
        direct[user] = [room for room in direct[user] if room != audit]
        if not direct[user]:
            del direct[user]
        request('PUT', account_data, direct, operator)
    deactivate_worker(data, 'root', settings)
    disconnect(data, 'root')


def archive_legacy(data):
    from uninstall import check_purge_mounts

    state = data / 'state/root'
    if not state.exists():
        return
    check_purge_mounts(state)
    archive = data / 'archive/agents/root'
    for directory_path in (data / 'archive', archive.parent, archive):
        directory(directory_path)
    gateway = state / 'matrix/gateway.sqlite3'
    if gateway.is_file():
        with closing(sqlite3.connect(gateway.as_uri() + '?mode=rw', uri=True)) as db, db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(tasks)')}
            if {'outcome', 'body', 'command'} <= columns:
                db.execute("UPDATE tasks SET outcome='superseded',body=NULL,command=NULL WHERE outcome IS NULL")
        for suffix in ('', '-wal', '-shm'):
            file = gateway.with_name(gateway.name + suffix)
            if file.is_file():
                file.rename(archive / file.name)
    execution = state / 'execution'
    if execution.is_dir():
        execution.rename(archive / 'execution')
    archive_worker(data, 'root')


def migrate_root(data, password_file, confirmed):
    journal = data / 'the-architect-migration.json'
    with operator_lock(data):
        settings, registry = open_existing(data)
        progress = load(journal) if journal.exists() else {}
        if progress.get('phase') == 'complete':
            print('The Architect is already provisioned.')
            return
        if 'root' not in registry['agents'] and not progress:
            raise ValueError('This deployment has no legacy Root agent to migrate')
        if not settings['systemd']:
            raise ValueError('The Architect requires systemd on the deployment host')
        if not progress:
            if 'the-architect' in registry['agents'] or 'the-architect' in registry.get('retired', {}):
                raise ValueError('Resolve the existing The Architect identity before migration')
            if not confirmed and (not sys.stdin.isatty() or input(
                    'Type migrate to retire Root and keep its Control Room chat: ').strip() != 'migrate'):
                raise ValueError('Migration requires confirmation. Use --yes for a scripted migration.')
            progress = {'phase': 'stopping'}
            save(journal, progress)
        rooms = load(data / 'rooms.json')
        operator = login(settings['owner'], password(settings, password_file))['access_token']
        try:
            if progress['phase'] == 'stopping':
                stop_legacy(data, settings)
                progress['phase'] = 'stopped'
                save(journal, progress)
            if progress['phase'] == 'stopped':
                revoke_legacy(data, settings, rooms, operator)
                progress['phase'] = 'revoked'
                save(journal, progress)
            archive_legacy(data)
            legacy = registry['agents'].pop('root', None)
            if legacy:
                registry.setdefault('retired', {})['root'] = {'name': legacy['name'],
                                                               'at': datetime.now(timezone.utc).isoformat()}
            settings['agents'] = ['the-architect' if key == 'root' else key for key in settings['agents']]
            overrides = settings.get('subscriptions', {}).get('overrides', {})
            if 'root' in overrides:
                overrides['the-architect'] = overrides.pop('root')
            if 'the-architect' not in registry['agents']:
                register_entry(settings, registry, 'the-architect', load(PRESETS)['the-architect'])
            compose_config(settings, data, registry)
            compose(data, 'build', 'context')
            refresh_context(data, retire='root')
            prepare_agent_state(data, 'the-architect', registry['agents']['the-architect'], rooms, operator, settings)
            prepare_images(settings, registry)
            up(data, 'engine-the-architect')
            sync_workshop(settings, data, registry)
            up(data, *(key for key, entry in registry['agents'].items()
                       if entry['placement'] == 'local' and entry['active']))
            commit_registry(data / 'agents.json', registry, actor=settings['owner'], reason='The Architect migration')
            save(data / 'settings.json', settings)
            publish_inventory(settings, data, registry)
            save(journal, {'phase': 'complete'})
        finally:
            request('POST', CLIENT + '/logout', {}, operator)
    print('Root was retired. Activate The Architect with agent configure the-architect.')
