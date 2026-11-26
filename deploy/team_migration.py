"""Opt-in transition to the five starter roles. Preview has no side effects."""
from contextlib import closing
import json
import sqlite3

from installer import (CLIENT, RUNTIME_UID, compose, compose_config, configure_network, directory, load, login,
                       open_existing, operator_lock, password, prepare_agent_state, prepare_images, prepare_storage,
                       publish_inventory, refresh_context, register_entry, request, save, service_units,
                       starter_profiles, sync_workshop, up)
from layout import encoded, room_route
from connection_history import commit_registry
from persona_management import disconnect
from roster import archive_worker, deactivate_worker, desk_pending, local_worker, prepare_management, retire_locked


def preview(registry, retire=()):
    entries = registry['agents']
    if 'root' in entries:
        raise ValueError('Run agent migrate-root before migrating this roster')
    coordinator = next((k for k, v in entries.items() if v['profile']['role'] == 'coordinator'), 'task-manager')
    if 'task-manager' in entries and (coordinator != 'task-manager' or entries['task-manager']['profile']['role'] != 'coordinator'):
        raise ValueError('Resolve the existing task-manager identity before migrating the coordinator')
    names = [coordinator if key == 'task-manager' else key for key in starter_profiles()]
    display = {v['name']: k for k, v in entries.items()}
    for key, preset in zip(names, starter_profiles().values()):
        if preset['name'] in display and display[preset['name']] != key:
            raise ValueError(f"Resolve the existing display name {preset['name']} before migration")
    for key in names:
        if key in registry.get('retired', {}):
            raise ValueError(f'Resolve the retired starter ID {key} before migration')
        if key in entries and key not in {coordinator, 'vps-manager'} and not entries[key].get('starter_role'):
            raise ValueError(f'{key} is an existing custom identity. Resolve the name collision before migration.')
    for key in retire:
        if key not in registry.get('retired', {}):
            if key in names or key == 'chief-of-staff':
                raise ValueError(f'{key} is part of the starter-team transition')
            local_worker(key, entries.get(key))
    return {'task_manager': coordinator, 'add': [k for k in names if k not in entries],
            'retain': sorted(set(entries) - set(retire) - {'chief-of-staff'}),
            'retire': sorted(set(retire)), 'archive_chief': 'chief-of-staff' in entries}


def open_work(data, entries):
    for key in entries:
        state = data / 'state' / key
        path = state / 'matrix/connector.sqlite3'
        if path.is_file():
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if db.execute('SELECT 1 FROM inbox WHERE sent IS NULL LIMIT 1').fetchone():
                    return key
                if 'context_outbox' in tables and db.execute('SELECT 1 FROM context_outbox LIMIT 1').fetchone():
                    return key
                columns = {row[1] for row in db.execute('PRAGMA table_info(inbox)')}
                if {'request','outgoing'} <= columns and db.execute("""SELECT 1 FROM inbox AS start WHERE start.id=start.request AND
                    json_extract(start.outgoing,'$.target') IS NOT NULL AND NOT EXISTS
                    (SELECT 1 FROM inbox AS final WHERE final.request=start.request AND
                     json_extract(final.outgoing,'$.content."net.matrix_agents.task_outcome".status') IS NOT NULL) LIMIT 1""").fetchone():
                    return key
        if key == 'chief-of-staff' and desk_pending(state / 'observer/reader.sqlite3'):
            return key
        if key == 'vps-manager' and (state / 'matrix/gateway.sqlite3').exists():
            with closing(sqlite3.connect((state / 'matrix/gateway.sqlite3').as_uri()+'?mode=ro', uri=True)) as db:
                if db.execute('SELECT 1 FROM tasks WHERE outcome IS NULL OR notified=0 OR '
                              '(source_thread IS NOT NULL AND source_notified=0) LIMIT 1').fetchone():
                    return key
    return None


def migrate(data, args):
    if not args.apply:
        print(json.dumps(preview(load(data / 'agents.json'), args.retire), indent=2))
        return
    path = data / 'team-migration.json'
    with operator_lock(data):
        settings, registry = open_existing(data)
        journal = load(path) if path.exists() else {}
        if journal.get('phase') == 'complete' or (not journal and registry.get('team_version') == 2):
            print('The starter-team migration is already complete.')
            return
        if not journal:
            plan = preview(registry, args.retire)
            if (data / 'onboarding.json').exists() and 'welcome_attempt' in load(data / 'onboarding.json'):
                raise ValueError('Finish the pending welcome before migrating')
            journal = {'phase': 'prepare', 'plan': plan}
            save(path, journal)
        plan = journal['plan']
        if args.retire and sorted(set(args.retire)) != plan['retire']:
            raise ValueError('Resume with the original retirement selection')
        owner = login(settings['owner'], password(settings, args.owner_password_file))['access_token']
        try:
            rooms = load(data / 'rooms.json')
            if journal['phase'] == 'prepare':
                active = [key for key, e in registry['agents'].items() if e['placement'] == 'local' and e['active']]
                local = [key for key, e in registry['agents'].items() if e['placement'] == 'local']
                if pending := open_work(data, local):
                    raise ValueError(f'Finish open assignments and decisions for {pending}, then resume migration')
                if active:
                    compose(data, '--profile', 'agents', 'stop', *active)
                if pending := open_work(data, local):
                    up(data, *active)
                    raise ValueError(f'An assignment arrived for {pending}. Finish it and resume.')
                journal['phase'] = 'stopped'
                save(path, journal)
            if journal['phase'] == 'stopped':
                if plan['archive_chief']:
                    key = 'chief-of-staff'
                    state = data / 'state' / key
                    room = (state / 'matrix/room-id').read_text().strip()
                    journal['chief_room'] = room
                    request('PUT', room_route(rooms['space']) + '/state/m.space.child/' + encoded(room), {}, owner)
                    deactivate_worker(data, key, settings)
                    disconnect(data, key)
                    compose(data, '--profile', 'agents', 'rm', '-sf', key, 'engine-' + key)
                old = data / 'state/chief-of-staff/persona'
                home = data / 'persona-workshop'
                if old.is_dir():
                    if home.exists():
                        raise ValueError('Two Workshop stores exist. Resolve their ownership before proceeding.')
                    compose(data, '--profile', 'agents', 'stop', 'persona-workshop')
                    old.rename(home)
                journal['phase'] = 'switch'
                save(path, journal)
            if journal['phase'] == 'switch':
                key = 'chief-of-staff'
                if plan['archive_chief'] and key in registry['agents']:
                    archive = data / 'archive/agents' / key
                    directory(archive)
                    for name in ('observer','persona-exports'):
                        item = data / 'state' / key / name
                        if item.exists():
                            item.rename(archive / name)
                    archive_worker(data, key)
                    del registry['agents'][key]
                    registry.setdefault('retired', {})[key] = {'name':'Chief of Staff', 'reason':'starter-team migration',
                                                               'room':journal['chief_room']}
                    (data / 'roster/bridge-key').unlink(missing_ok=True)
                registry.update(team_version=2, dynamic_handoffs=True)
                presets = starter_profiles()
                for role, preset in presets.items():
                    key = plan['task_manager'] if role == 'task-manager' else role
                    if key in registry['agents']:
                        registry['agents'][key].update(name=preset['name'], profile=preset['profile'], starter_role=role)
                    else:
                        register_entry(settings, registry, key, preset)
                settings['agents'] = [k for k in settings['agents'] if k in registry['agents']]
                settings['agents'] += [k for k in plan['add'] if k not in settings['agents']]
                settings.get('subscriptions', {}).get('overrides', {}).pop('chief-of-staff', None)
                settings['roster_management'] = False
                commit_registry(data / 'agents.json', registry, actor=settings['owner'], reason='starter-team migration')
                save(data / 'settings.json', settings)
                journal['phase'] = 'provision'
                save(path, journal)
            if journal['phase'] == 'provision':
                prepare_storage(settings,data)
                compose_config(settings,data,registry)
                refresh_context(data, retire='chief-of-staff')
                for key, entry in registry['agents'].items():
                    if entry['placement'] == 'local':
                        prepare_agent_state(data, key, entry, rooms, owner, settings)
                manager = plan['task_manager']
                access = (data / 'state' / manager / 'matrix/matrix-token').read_text().strip()
                request('PUT', CLIENT + '/profile/' + encoded('@' + manager + ':' + settings['server_name']) +
                        '/displayname', {'displayname': 'Task Manager'}, access)
                prepare_management(data,settings,registry,owner)
                prepare_images(settings,registry)
                local = [k for k,e in registry['agents'].items() if e['placement']=='local']
                up(data,*('engine-'+k for k in local))
                sync_workshop(settings,data,registry)
                active = [key for key in local if registry['agents'][key]['active']]
                up(data, *active, *(['helper-dm'] if 'helper' in active else []))
                service_units(settings,data,broker=registry['agents'].get('vps-manager',{}).get('active',False))
                configure_network(settings,data,settings)
                journal['phase'] = 'retire'
                save(path,journal)
            for key in plan['retire']:
                if retire_locked(data,settings,registry,key) != 'retired':
                    raise ValueError(f'{key} is draining. Resume the migration after it finishes.')
            publish_inventory(settings,data,registry)
            save(path, {**journal,'phase':'complete'})
        finally:
            request('POST', CLIENT + '/logout',{},owner)
    print('Starter team provisioned. Run ./constellation_matrix agent configure to activate pending agents.')
