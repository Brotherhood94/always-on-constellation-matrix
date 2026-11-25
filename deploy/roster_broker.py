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
from roster import local_worker, reconcile_retirements, retire_locked

WINDOW = 15 * 60_000
MAX_MESSAGE = 24_000
EVENT_ID = re.compile(r'\$[^\s\x00-\x1f]{1,254}')


def digest(value):
    return sha256(value.encode()).hexdigest()


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
    if not isinstance(operation, dict) or operation.get('kind') not in ('add', 'retire'):
        raise ValueError('Choose an add or retire operation')
    key = operation.get('agent')
    if operation['kind'] == 'add':
        profile = operation.get('profile')
        if (set(operation) != {'kind', 'agent', 'profile'} or not isinstance(profile, dict) or
                set(profile) != {'key', 'name', 'model', 'effort', 'profile'} or profile.get('key') != key or
                not isinstance(profile.get('profile'), dict) or
                set(profile['profile']) != {'role', 'limits', 'capabilities'} or
                not all(isinstance(profile[field], str) for field in ('name', 'model', 'effort'))):
            raise ValueError('A hire requires a standard local worker profile')
        local_worker(key, {**profile, 'placement': 'local'})
    elif set(operation) != {'kind', 'agent'} or not isinstance(key, str):
        raise ValueError('Invalid retirement target')
    return identifier, operation


def verify(data, settings, payload, now):
    chief = f"@chief-of-staff:{settings['server_name']}"
    owner = f"@{settings['owner']}:{settings['server_name']}"
    matrix = data / 'state/chief-of-staff/matrix'
    room = (matrix / 'room-id').read_text().strip()
    token = read_password_file(matrix / 'matrix-token', 'Chief Matrix')
    route = room_route(room)
    state = request('GET', route + '/state', token=token)
    rules = next((item['content'] for item in state if item['type'] == 'm.room.join_rules'), {})
    members = {item['state_key']: item['content']['membership'] for item in state if item['type'] == 'm.room.member'
               and item['content'].get('membership') in {'invite', 'join'}}
    if rules.get('join_rule') != 'invite' or members != {chief: 'join', owner: 'join'}:
        raise ValueError('Roster approval requires the private Chief of Staff DM')
    stamps = []
    for field, sender in [('request', owner), ('proposal', chief), ('poll', chief), ('vote', owner)]:
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
    secret = read_password_file(root / 'bridge-key', 'Roster bridge')
    identifier, operation = validate(payload, secret, now)
    key = operation['agent']
    receipt = {field: payload[field] for field in ('request', 'proposal', 'poll', 'vote', 'decided', 'operation')}
    with operator_lock(data):
        settings, registry = open_existing(data)
        if not settings.get('roster_management') or not registry['agents'].get('chief-of-staff', {}).get('active'):
            raise ValueError('Roster management is disabled')
        records = root / 'requests'
        directory(records)
        path = records / (identifier + '.json')
        if path.exists():
            record = load(path)
            if record['receipt'] != receipt:
                raise ValueError('This request already has a different approval receipt')
            if record['status'] != 'pending':
                return {'status': record['status'], 'agent': key}
        else:
            verify(data, settings, payload, now)
            if operation['kind'] == 'retire':
                local_worker(key, registry['agents'].get(key))
            elif key in registry['agents'] or key in registry.get('retired', {}):
                raise ValueError('Choose a new worker ID')
            save(path, {'status': 'pending', 'receipt': receipt})
        if operation['kind'] == 'add':
            token = read_password_file(root / 'inviter-token', 'Office invitation')
            provision_agent(data, settings, registry, operation['profile'], token)
            status = 'provisioned'
        else:
            status = retire_locked(data, settings, registry, key)
        save(path, {'status': 'pending' if status == 'draining' else status, 'receipt': receipt})
        return {'status': status, 'agent': key}


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
    else:
        with socket.socket(fileno=0) as connection:
            serve(data, connection)
