"""Apply one operator-approved, typed roster operation over the private host socket."""
from hashlib import sha256
from hmac import compare_digest
import json
from pathlib import Path
import re
import socket
import sqlite3
import struct
from subprocess import SubprocessError
import sys
import time
from urllib.parse import quote

from installer import RUNTIME_UID, directory, load, operator_lock, open_existing, provision_agent, read_password_file, request, save
from layout import room_route
from provision import MatrixError
from agent_catalog import STARTER_ROLES
from team_management import apply_tool, coach_change, coaching_id, tool_spec
from roster import local_worker, reconcile_retirements, retire_locked
import hiring

WINDOW = 15 * 60_000
MAX_MESSAGE = 24_000
EVENT_ID = re.compile(r'\$[^\s\x00-\x1f]{1,254}')


def digest(value):
    return sha256(value.encode()).hexdigest()


def authorized_actor(settings, registry, actor):
    entry = registry['agents'].get(actor, {})
    if not settings.get('roster_management') or not entry.get('active') or entry.get('retirement'):
        return False
    if actor == 'chief-of-staff':
        return 'hiring-manager' not in registry['agents'] and registry.get('team_version') != 2
    return registry.get('team_version') == 2 and entry.get('starter_role') == actor


def validate(payload, key, now):
    if not isinstance(payload, dict) or not isinstance(payload.get('key'), str) or not compare_digest(
            payload['key'].encode(), key.encode()):
        raise ValueError('Invalid roster bridge credential')
    events = [payload.get(field) for field in ('request', 'proposal', 'poll', 'vote')]
    if (not all(isinstance(event, str) and EVENT_ID.fullmatch(event) for event in events) or
            len(set(events)) != len(events)):
        raise ValueError('Invalid roster event references')
    proposal = payload.get('operation')
    if not isinstance(proposal, str) or len(proposal.encode()) > 12_000:
        raise ValueError('Invalid roster proposal')
    identifier = digest(payload['request'])
    choice = json.dumps([identifier, digest(proposal), payload['proposal'], 'approve'], separators=(',', ':'), ensure_ascii=False)
    if payload.get('choice') != digest(choice) or type(payload.get('decided')) is not int or payload['decided'] > now + 60_000:
        raise ValueError('The vote does not approve this proposal')
    operation = json.loads(proposal)
    actor = payload.get('actor', 'chief-of-staff')
    allowed = {'chief-of-staff': {'add', 'retire'}, 'hiring-manager': {'add', 'retire'},
               'scouter': {'tool'}, 'coach': {'coach', 'undo'}}
    if not isinstance(operation, dict) or operation.get('kind') not in allowed.get(actor, set()):
        raise ValueError('Choose an add or retire operation')
    key = operation.get('agent')
    if operation['kind'] == 'add':
        profile = operation.get('profile')
        managed = 'provider' in operation
        fields = {'kind', 'agent', 'profile'} | ({'provider', 'tools', 'firstTask'} if managed else set())
        if 'resume' in operation and managed and operation['resume'] is True:
            fields.add('resume')
        if (set(operation) != fields or not isinstance(profile, dict) or
                set(profile) != {'key', 'name', 'model', 'effort', 'profile'} or profile.get('key') != key or
                not isinstance(profile.get('profile'), dict) or
                set(profile['profile']) not in ({'role', 'limits', 'capabilities'}, {'role', 'limits', 'capabilities', 'persona', 'contacts'}) or
                not all(isinstance(profile[field], str) for field in ('name', 'model', 'effort'))):
            raise ValueError('A hire requires a standard local worker profile')
        local_worker(key, {**profile, 'placement': 'local'})
        if managed:
            provider = operation['provider']
            grants = operation['tools']
            task = operation['firstTask']
            if (actor != 'hiring-manager' or not isinstance(provider, dict)
                    or set(provider) != {'mode', 'account', 'label'} or provider['mode'] not in hiring.MODES
                    or not isinstance(provider['account'], str) or not re.fullmatch('[a-f0-9]{24}', provider['account'])
                    or not isinstance(provider['label'], str) or not provider['label'].isprintable()
                    or not 1 <= len(provider['label']) <= 100
                    or not isinstance(grants, list) or len(grants) > 5
                    or not isinstance(task, str) or not 1 <= len(task) <= 1400):
                raise ValueError('Invalid managed hire profile')
            verified = [tool_spec(tool) for tool in grants]
            if len({tool['id'] for tool in verified}) != len(verified):
                raise ValueError('Duplicate hiring tool grant')
        if 'contacts' in profile['profile'] and profile['profile']['contacts'] != ['task-manager']:
            raise ValueError('A new specialist starts through Task Manager')
    elif not isinstance(key, str) or not re.fullmatch('[a-z0-9][a-z0-9_-]{0,35}', key):
        raise ValueError('Invalid agent target')
    elif operation['kind'] == 'tool':
        if set(operation) != {'kind', 'agent', 'tool'}:
            raise ValueError('Invalid tool assignment')
        tool_spec(operation['tool'])
    elif operation['kind'] == 'retire' and (set(operation) != {'kind', 'agent'} or key == actor):
        raise ValueError('Invalid retirement target')
    return identifier, operation


def private_room(data, settings, actor):
    chief = f"@{actor}:{settings['server_name']}"
    owner = f"@{settings['owner']}:{settings['server_name']}"
    matrix = data / 'state' / actor / 'matrix'
    room = (matrix / ('room-id' if actor == 'chief-of-staff' else 'control-room-id')).read_text().strip()
    token = read_password_file(matrix / 'matrix-token', 'Chief Matrix')
    route = room_route(room)
    state = request('GET', route + '/state', token=token)
    rules = next((item['content'] for item in state if item['type'] == 'm.room.join_rules'), {})
    members = {item['state_key']: item['content']['membership'] for item in state if item['type'] == 'm.room.member'
               and item['content'].get('membership') in {'invite', 'join'}}
    encryption = next((item['content'] for item in state if item['type'] == 'm.room.encryption'), {})
    if (rules.get('join_rule') != 'invite' or members != {chief: 'join', owner: 'join'}
            or encryption.get('algorithm') != 'm.megolm.v1.aes-sha2'):
        raise ValueError('Roster approval requires the role’s encrypted operator DM')
    return chief, owner, room, token, route


def verify(data, settings, payload, now):
    actor = payload.get('actor', 'chief-of-staff')
    chief, owner, room, token, route = private_room(data, settings, actor)
    stamps = []
    for field, sender in [('request', chief if payload.get('initiated') == 'agent' and actor != 'chief-of-staff' else owner), ('proposal', chief), ('poll', chief), ('vote', owner)]:
        event = request('GET', route + '/event/' + quote(payload[field], safe=''), token=token)
        stamp = event.get('origin_server_ts')
        if (event.get('event_id') != payload[field] or event.get('room_id', room) != room or
                event.get('sender') != sender or event.get('type') != 'm.room.encrypted' or
                event.get('unsigned', {}).get('redacted_because') or type(stamp) is not int):
            raise ValueError('Matrix could not verify the encrypted roster receipt')
        stamps.append(stamp)
    relation = event.get('content', {}).get('m.relates_to', {})
    if (stamps != sorted(stamps) or stamps[-1] != payload['decided'] or stamps[3] > stamps[1] + WINDOW
            or now > stamps[1] + WINDOW or relation.get('rel_type') != 'm.reference'
            or relation.get('event_id') != payload['poll']):
        raise ValueError('The approval expired or points to a different poll')


def handle(data, payload, *, now=None):
    now = int(time.time() * 1000) if now is None else now
    root = data / 'roster'
    actor = payload.get('actor', 'chief-of-staff')
    if actor not in {'chief-of-staff', 'hiring-manager', 'coach', 'scouter'}:
        raise ValueError('Unknown management identity')
    secret = read_password_file(root / ('bridge-key' if actor == 'chief-of-staff' else actor + '.key'), 'Role bridge')
    if 'hiring' in payload:
        if actor != 'hiring-manager' or not isinstance(payload.get('key'), str) or not compare_digest(payload['key'], secret):
            raise ValueError('Only Hiring Manager can access these connections')
        settings, registry = open_existing(data)
        if not authorized_actor(settings, registry, actor):
            raise ValueError('Hiring Manager is unavailable')
        command = payload['hiring']
        action = command['action']
        if action == 'begin':
            _, owner, room, token, route = private_room(data, settings, actor)
            event_id = command['event']
            if not isinstance(event_id, str) or not EVENT_ID.fullmatch(event_id):
                raise ValueError('Choose a verified operator selection')
            event = request('GET', route + '/event/' + quote(event_id, safe=''), token=token)
            stamp = event.get('origin_server_ts')
            if (event.get('event_id') != event_id or event.get('sender') != owner or event.get('room_id', room) != room
                    or event.get('type') != 'm.room.encrypted' or event.get('unsigned', {}).get('redacted_because')
                    or type(stamp) is not int or not now - WINDOW <= stamp <= now + 60_000):
                raise ValueError('The sign-in must start from the encrypted operator choice')
            command = {'action': action, 'id': digest(event_id)[:24], 'mode': command['mode'], 'account': command.get('account')}
        elif action == 'pending':
            candidate = command['agent']
            if not isinstance(candidate, str) or not re.fullmatch('[a-z0-9][a-z0-9_-]{0,35}', candidate):
                raise ValueError('Invalid pending hire')
            entry = registry['agents'].get(candidate)
            if (not entry or entry['active'] or entry.get('hiring_provider') or entry.get('retirement')
                    or not mark_legacy_hire(data / 'roster/requests', entry, candidate)):
                return {}
            return {'profile': {'key': candidate, **{field: entry[field] for field in ('name', 'model', 'effort', 'profile')}}}
        elif action == 'catalog':
            command = {'action': action, 'account': command['account']}
        elif action == 'login-status':
            command = {'action': action, 'id': command['id']}
        elif action in {'accounts', 'tools'}:
            command = {'action': action}
        else:
            raise ValueError('Unknown hiring connection operation')
        return hiring.control_request(data, command)
    if actor == 'coach' and payload.get('routine') is True:
        if not isinstance(payload.get('key'), str) or not compare_digest(payload['key'].encode(), secret.encode()):
            raise ValueError('Invalid Coach credential')
        with operator_lock(data):
            settings, registry = open_existing(data)
            if not authorized_actor(settings, registry, actor):
                raise ValueError('Coach is unavailable')
            operation = payload['operation']
            status = coach_change(data, settings, registry, operation)
            return {'status': status, 'change': coaching_id(operation)}
    identifier, operation = validate(payload, secret, now)
    key = operation['agent']
    receipt = {field: payload[field] for field in ('request', 'proposal', 'poll', 'vote', 'decided', 'operation')}
    with operator_lock(data):
        settings, registry = open_existing(data)
        if not authorized_actor(settings, registry, actor):
            raise ValueError('Roster management is disabled')
        records = root / 'requests'
        directory(records)
        path = records / (identifier + '.json')
        if path.exists():
            record = load(path)
            if record['receipt'] != receipt:
                raise ValueError('This request already has a different approval receipt')
            if record.get('managed'):
                return {'status': 'activating' if record['status'] == 'pending' else record['status'], 'agent': key}
            if record['status'] != 'pending':
                return {'status': record['status'], 'agent': key, **({'change':coaching_id(operation)} if actor == 'coach' else {})}
        else:
            verify(data, settings, payload, now)
            if operation['kind'] == 'retire':
                if actor == 'hiring-manager' and registry.get('team_version') == 2 and key in (STARTER_ROLES | {'manager'}):
                    if key == actor or key not in registry['agents']:
                        raise ValueError('Only the operator can retire Hiring Manager')
                else:
                    local_worker(key, registry['agents'].get(key))
            elif operation['kind'] == 'add':
                existing = registry['agents'].get(key)
                if operation.get('resume') and (not existing or existing['active'] or existing.get('retirement')
                                                or existing.get('hiring_provider') or existing.get('mobility_move')
                                                or not mark_legacy_hire(records, existing, key)):
                    raise ValueError('Choose an existing pending hire')
                if not operation.get('resume') and (existing or key in registry.get('retired', {})):
                    raise ValueError('Choose a new worker ID')
                if 'provider' in operation:
                    for prior in records.glob('*.json'):
                        saved = load(prior)
                        if (saved.get('managed') and saved['status'] in hiring.PENDING
                                and json.loads(saved['receipt']['operation']).get('agent') == key):
                            raise ValueError('A different approved hire already holds this worker ID')
                    if operation.get('resume'):
                        from installer import profile as validated_profile
                        if (existing['name'] != operation['profile']['name'] or
                                existing['profile'] != validated_profile(operation['profile']['profile'])):
                            raise ValueError('The pending profile changed since review')
                        save(data / 'agents.json', registry)
                    save(path, {'status': 'pending', 'receipt': receipt, 'managed': True})
                    return {'status': 'activating', 'agent': key}
            save(path, {'status': 'pending', 'receipt': receipt})
        if operation['kind'] == 'add':
            token = read_password_file(root / 'inviter-token', 'Office invitation')
            provision_agent(data, settings, registry, operation['profile'], token, actor=actor, approval=identifier)
            registry['agents'][key]['legacy_hire_approval'] = identifier
            save(data / 'agents.json', registry)
            status = 'provisioned'
        elif operation['kind'] == 'tool':
            status = apply_tool(data, settings, registry, operation, identifier)
        elif operation['kind'] in {'coach', 'undo'}:
            status = coach_change(data, settings, registry, operation, approved=True, approval=identifier)
        else:
            status = retire_locked(data, settings, registry, key, actor=actor, approval=identifier)
        save(path, {'status': 'pending' if status in {'draining', 'checking'} else status, 'receipt': receipt})
        return {'status': status, 'agent': key, **({'change':coaching_id(operation)} if actor == 'coach' else {})}


def mark_legacy_hire(records, entry, key):
    if entry.get('legacy_hire_approval'):
        return True
    for path in records.glob('*.json'):
        raw = load(path)
        if raw['status'] == 'provisioned':
            proposed = json.loads(raw['receipt']['operation'])
            if proposed.get('kind') == 'add' and proposed.get('agent') == key:
                entry['legacy_hire_approval'] = path.stem
                return True
    return False


def serve(data, peer):
    uid = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))[1]
    if uid != RUNTIME_UID:
        return
    peer.settimeout(8)
    with peer.makefile('rb') as stream:
        line = stream.readline(MAX_MESSAGE + 1)
        if not line.endswith(b'\n') or len(line) > MAX_MESSAGE:
            return
        try:
            result = handle(data, json.loads(line))
        except (ValueError, KeyError, TypeError) as error:
            print(f'Roster request rejected ({type(error).__name__}).', file=sys.stderr)
            result = {'status': 'rejected', 'reason': 'The approved change is invalid, expired, or conflicts with the roster.'}
        except (MatrixError, OSError, SubprocessError, sqlite3.Error) as error:
            print(f'Roster operation will retry ({type(error).__name__}).', file=sys.stderr)
            result = {'status': 'retry'}
        peer.sendall(json.dumps(result).encode() + b'\n')


if __name__ == '__main__':
    data = Path(sys.argv[1])
    if sys.argv[2:] == ['reconcile']:
        reconcile_retirements(data)
        hiring.reconcile(data)
    else:
        with socket.socket(fileno=0) as connection:
            serve(data, connection)
