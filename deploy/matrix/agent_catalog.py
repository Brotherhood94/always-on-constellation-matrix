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
    return dict(role=role, limits=limits.strip(), capabilities=skills)


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


def document(registry, inventory, server_name, now):
    if inventory is None:
        raise ValueError('The resource inventory is unavailable')
    observed = {(row['name'], row.get('server', registry['local_server'])): row for row in inventory}
    result = []
    for key, entry in sorted(registry['agents'].items()):
        if 'profile' not in entry:
            continue
        if not KEY.fullmatch(key) or not clean(entry['name'], 64) or not clean(entry['server'], 253):
            raise ValueError('An agent profile has an invalid identity or host')
        host = entry['server']
        current = observed.get((entry['name'], host), {})
        default = 'Unavailable' if host == registry['local_server'] else 'Registered'
        result.append(dict(key=key, id=f'@{key}:{server_name}', name=entry['name'], server=host,
                           status=current.get('status', default), active=entry.get('active', True), model=current.get('model', entry.get('model')),
                           effort=current.get('effort', entry.get('effort')), **profile(entry['profile'])))
    if any(sum(row['role'] == role for row in result) > 1 for role in ('coordinator', 'observer')):
        raise ValueError('There must be a single active coordinator and observer')
    return dict(version=1, time=now, dynamicHandoffs=registry.get('dynamic_handoffs') is True, agents=result)


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
