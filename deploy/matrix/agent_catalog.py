#!/usr/bin/env python3
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile


REGISTRY = Path(os.environ.get('MATRIX_REGISTRY', '/srv/constellation-matrix/agents.json'))
KEY = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')
RESOURCE = re.compile(r'[a-z][a-z0-9-]{0,30}:(?:\*|[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119})')
ROLES = {'worker', 'coordinator', 'observer'}


def clean(value, limit):
    return isinstance(value, str) and 0 < len(value.strip()) <= limit and value.isprintable()


STARTER_ROLES = {'task-manager', 'coach', 'scouter', 'hiring-manager', 'vps-manager'}
PRIVATE_ROLES = {'hiring-manager', 'coach', 'scouter'}


def persona_text(value):
    if (not isinstance(value, dict) or not clean(value.get('purpose'), 600)
            or not clean(value.get('working_style'), 1200) or not isinstance(value.get('duties'), list)
            or not 1 <= len(value['duties']) <= 16 or not all(clean(item, 400) for item in value['duties'])):
        raise ValueError('A persona needs a purpose, duties and working style')
    return {key: value[key] for key in ('purpose', 'duties', 'working_style')}


def contact_keys(value):
    if not isinstance(value, list) or len(value) > 128 or not all(isinstance(k, str) and KEY.fullmatch(k) for k in value):
        raise ValueError('Use a list of agent IDs for contacts')
    return list(dict.fromkeys(value))


def role_id(key, entry):
    if entry['profile']['role'] == 'coordinator':
        return 'task-manager'
    return key if key == 'vps-manager' or entry.get('starter_role') == key and key in STARTER_ROLES else 'specialist'


def persona_for(entry):
    configured = entry['profile']
    return persona_text(configured['persona']) if 'persona' in configured else {
        'purpose': configured['capabilities'][0]['description'],
        'duties': [item['description'] for item in configured['capabilities']],
        'working_style': configured['limits'],
    }


def contacts_for(key, entries):
    entry = entries[key]
    manager = next((k for k, v in entries.items() if v['profile']['role'] == 'coordinator'), None)
    if 'office_contacts' in entry:
        contacts = entry['office_contacts']
    else:
        contacts = list(entry['profile'].get('contacts', ['task-manager']))
        if key == manager:
            contacts.extend(k for k, v in entries.items() if role_id(k, v) == 'specialist')
    keys = [manager if k == 'task-manager' else k for k in contacts]
    return sorted({k for k in keys if k in entries and k != key and not entries[k].get('retirement')})


def profile(value):
    role = value['role']
    limits = value['limits']
    offered = value['capabilities']
    if role not in ROLES or not clean(limits, 400) or not isinstance(offered, list) or not 1 <= len(offered) <= 16:
        raise ValueError('Invalid role, limits, or capabilities')
    skills = []
    for skill in offered:
        code, description, resources = (skill[key] for key in ('code', 'description', 'resources'))
        if (not KEY.fullmatch(code) or not clean(description, 200) or not isinstance(resources, list)
                or not 1 <= len(resources) <= 20 or not all(RESOURCE.fullmatch(item) for item in resources)):
            raise ValueError('Invalid capability or resource scope')
        skills.append(dict(code=code, description=description.strip(), resources=sorted(set(resources))))
    if len({skill['code'] for skill in skills}) != len(skills):
        raise ValueError('Duplicate capability code')
    return dict(role=role, limits=limits.strip(), capabilities=skills,
                **({'persona': persona_text(value['persona'])} if 'persona' in value else {}),
                **({'contacts': contact_keys(value['contacts'])} if 'contacts' in value else {}))


def read(path=REGISTRY):
    if not path.exists():
        return {'local_server': os.environ.get('MATRIX_LOCAL_HOST', 'localhost'), 'agents': {}}
    return json.loads(path.read_text())


def modify(change, path=REGISTRY):
    with path.with_name('.agent-registry.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        registry = read(path)
        change(registry)
        with NamedTemporaryFile('w', dir=path.parent, prefix='.agent-placement.', delete=False) as stream:
            pending = Path(stream.name)
            try:
                json.dump(registry, stream, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
                pending.chmod(0o640)
                pending.replace(path)
            finally:
                pending.unlink(missing_ok=True)


def register(registry, key, definition, name=None, host=None):
    if not KEY.fullmatch(key):
        raise ValueError('Invalid Matrix account localpart')
    entry = registry['agents'].get(key)
    if entry is None:
        if not (clean(name, 64) and clean(host, 253)):
            raise ValueError('Register the agent identity and host first or provide --name and --host')
        entry = registry['agents'][key] = {'name': name, 'server': host}
    if (name and entry.get('name') != name) or (host and entry.get('server') != host):
        raise ValueError(f'Conflicting identity for {key}')
    entry['profile'] = profile(definition)


def workshop_profiles(data):
    path = data / 'workshop-management/targets.json'
    if not path.is_file():
        return {}
    return {key: value.get('public', {}) for key, value in json.loads(path.read_text()).items()}


def document(registry, inventory, server_name, now, workshop=None):
    if inventory is None:
        raise ValueError('The resource inventory is unavailable')
    observed = {(row['name'], row.get('server', registry['local_server'])): row for row in inventory}
    result = []
    entries = {k: v for k, v in registry['agents'].items() if 'profile' in v}
    governed = registry.get('team_version') == 2
    for key, entry in sorted(entries.items()):
        if 'profile' not in entry:
            continue
        if not KEY.fullmatch(key) or not clean(entry['name'], 64) or not clean(entry['server'], 253):
            raise ValueError('An agent profile has an invalid identity or host')
        host = entry['server']
        current = observed.get((entry['name'], host), {})
        amplifier = entry.get('amplifier', {})
        default = 'Unavailable' if host == registry['local_server'] else 'Registered'
        active = entry.get('active', True)
        status = current.get('status', default) if active else 'Sign-in required'
        if entry.get('retirement'):
            status = 'Retiring'
        result.append(dict(key=key, id=f'@{key}:{server_name}', name=entry['name'], server=host,
                           status=status, active=active, placement=entry.get('placement', 'local'),
                           model=amplifier.get('model', current.get('model', entry.get('model'))),
                           effort=amplifier.get('effort', current.get('effort', entry.get('effort'))),
                           **profile(entry['profile'])))
        row = result[-1]
        row.update(persona=persona_for(entry), roleId=role_id(key, entry),
                   revision=entry.get('persona_revision'), tools=list(entry.get('enabled_tools', {})))
        if governed:
            row['contacts'] = contacts_for(key, entries)
            row['grants'] = entry.get('enabled_tools', {})
            row['toolChecks'] = entry.get('tool_checks', {})
        else:
            row['contacts'] = sorted(k for k, e in entries.items() if k != key and e['profile']['role'] != 'observer')
        if entry.get('applied_style'):
            row['persona']['working_style'] = entry['applied_style']
        installed = (workshop or {}).get(key, {})
        if installed.get('revision', -1) >= (entry.get('persona_revision') or 0):
            row['revision'] = installed['revision']
            row['persona']['working_style'] = installed.get('working_style', persona_for(entry)['working_style'])
    if any(sum(row['role'] == role for row in result) > 1 for role in ('coordinator', 'observer')):
        raise ValueError('There must be a single active coordinator and observer')
    return dict(version=1, contactPolicy=governed, time=now, dynamicHandoffs=registry.get('dynamic_handoffs') is True, agents=result)


def main():
    parser = argparse.ArgumentParser(description='Manage verified capabilities in the Matrix agent inventory.')
    commands = parser.add_subparsers(dest='operation', required=True)
    add = commands.add_parser('register')
    add.add_argument('agent')
    add.add_argument('profile', type=Path)
    add.add_argument('--name')
    add.add_argument('--host')
    seed = commands.add_parser('seed')
    seed.add_argument('profiles', type=Path)
    seed.add_argument('ids', nargs='*')
    remove = commands.add_parser('remove')
    remove.add_argument('agent')
    commands.add_parser('enable')
    commands.add_parser('disable')
    args = parser.parse_args()

    def change(registry):
        if args.operation == 'register':
            register(registry, args.agent, json.loads(args.profile.read_text()), args.name, args.host)
        elif args.operation == 'seed':
            for key, value in json.loads(args.profiles.read_text()).items():
                if (not args.ids or key in args.ids) and 'profile' not in registry['agents'].get(key, {}):
                    register(registry, key, value['profile'], value['name'], value['server'])
        elif args.operation == 'remove':
            registry['agents'].pop(args.agent, None)
        else:
            registry['dynamic_handoffs'] = args.operation == 'enable'
        document(registry, [], 'validation.invalid', 0)

    modify(change)


if __name__ == '__main__':
    main()
