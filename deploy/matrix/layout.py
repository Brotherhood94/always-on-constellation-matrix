"""Provision encrypted rooms and per-service Matrix identities."""
from urllib.parse import quote

from provision import MatrixError, register, request

CLIENT = '/_matrix/client/v3'
MISSING = {'M_NOT_FOUND', 'M_FORBIDDEN'}


def encoded(value):
    return quote(value, safe='')


def missing(path, token=None):
    try:
        return request('GET', path, token=token)
    except MatrixError as error:
        if error.code not in MISSING:
            raise
        return None


def room_route(room_id):
    return f'{CLIENT}/rooms/{encoded(room_id)}'


def ensure_room(server, alias, name, token, *, space=False, direct=None, pin_user=None):
    directory = f'{CLIENT}/directory/room/{encoded(f"#{alias}:{server}")}'
    found = missing(directory, token)
    if not found:
        state = [] if space else [
            {'type': 'm.room.encryption', 'state_key': '', 'content': {'algorithm': 'm.megolm.v1.aes-sha2'}},
            {'type': 'm.room.history_visibility', 'state_key': '', 'content': {'history_visibility': 'joined'}},
        ]
        creation = {'m.federate': False}
        if space:
            creation['type'] = 'm.space'
        body = {
            'room_alias_name': alias, 'preset': 'private_chat', 'visibility': 'private',
            'creation_content': creation, 'initial_state': state,
            'power_level_content_override': {'invite': 100, 'kick': 100, 'ban': 100},
        }
        if name:
            body['name'] = name
        if direct:
            body.update(is_direct=True, invite=[direct])
        if pin_user:
            body['power_level_content_override'].update(
                users={pin_user: 50}, events={'m.room.pinned_events': 50})
        found = request('POST', CLIENT + '/createRoom', body, token)
    room_id = found['room_id']
    route = room_route(room_id) + '/state/'
    creation = request('GET', route + 'm.room.create', token=token)
    audience = request('GET', route + 'm.room.join_rules', token=token)
    if creation.get('m.federate') is not False or audience.get('join_rule') != 'invite':
        raise ValueError(f'Existing {alias} has an unexpected audience')
    if space and creation.get('type') != 'm.space':
        raise ValueError(f'Existing {alias} is not a Space')
    if not space:
        encryption = request('GET', route + 'm.room.encryption', token=token)
        if encryption.get('algorithm') != 'm.megolm.v1.aes-sha2':
            raise ValueError(f'Existing {alias} is not encrypted')
    return room_id


def link_child(space, room, server, token):
    path = f'{room_route(space)}/state/m.space.child/{encoded(room)}'
    desired = {'via': [server]}
    if missing(path, token) != desired:
        request('PUT', path, desired, token)


def membership(room, user, token):
    result = missing(f'{room_route(room)}/state/m.room.member/{encoded(user)}', token)
    return (result or {}).get('membership')


def invite(room, user, token):
    if membership(room, user, token) not in {'invite', 'join'}:
        request('POST', room_route(room) + '/invite', {'user_id': user}, token)


def join(room, user, token):
    if room not in request('GET', CLIENT + '/joined_rooms', token=token)['joined_rooms']:
        result = request('POST', CLIENT + '/join/' + encoded(room), {}, token)
        if result.get('room_id') != room:
            raise ValueError(f'Unexpected Matrix room for {user}')


def direct_account_data(user, peer, room, token):
    path = f'{CLIENT}/user/{encoded(user)}/account_data/m.direct'
    current = missing(path, token) or {}
    if room not in current.get(peer, []):
        request('PUT', path, {**current, peer: [*current.get(peer, []), room]}, token)


def reconcile_direct_account_data(user, assignments, token):
    """Replace direct-chat mappings for managed rooms. A None peer means Rooms."""
    path = f'{CLIENT}/user/{encoded(user)}/account_data/m.direct'
    current = missing(path, token) or {}
    updated = {peer: [room for room in rooms if room not in assignments] if isinstance(rooms, list) else rooms
               for peer, rooms in current.items()}
    for room, peer in assignments.items():
        if peer and isinstance(updated.setdefault(peer, []), list):
            updated[peer].append(room)
    updated = {peer: rooms for peer, rooms in updated.items() if rooms or current.get(peer) == rooms}
    if updated != current:
        request('PUT', path, updated, token)


def direct_room(server, space, key, label, owner, operator, peer, token, *, classify=True, pin_user=None):
    room = ensure_room(server, key + '-dm', label, operator, direct=peer if classify else None, pin_user=pin_user)
    active = {event['state_key'] for event in request('GET', room_route(room) + '/state', token=operator)
              if event['type'] == 'm.room.member' and event['content'].get('membership') in {'invite', 'join'}}
    if active - {owner, peer}:
        raise ValueError(f'Unexpected participant in {label}')
    invite(room, peer, operator)
    join(room, peer, token)
    reconcile_direct_account_data(owner, {room: peer if classify else None}, operator)
    reconcile_direct_account_data(peer, {room: owner if classify else None}, token)
    link_child(space, room, server, operator)
    return room


def login(username, password, device=None, display=None):
    try:
        registration = register(username, password)
    except MatrixError:
        raise
    if not device:
        return registration
    request('POST', CLIENT + '/logout', {}, registration['access_token'])
    return request('POST', CLIENT + '/login', {
        'type': 'm.login.password', 'identifier': {'type': 'm.id.user', 'user': username},
        'password': password, 'device_id': device, 'initial_device_display_name': display or device,
    })


def create_layout(server, owner, operator):
    monitor = f'@monitor:{server}'
    space = ensure_room(server, 'agents', 'Agents', operator, space=True)
    office = ensure_room(server, 'office', 'Office', operator)
    alerts = ensure_room(server, 'vps-alerts', 'VPS Alerts', operator, pin_user=monitor)
    link_child(space, office, server, operator)
    link_child(space, alerts, server, operator)
    invite(alerts, monitor, operator)
    return {'space': space, 'office': office, 'alerts': alerts, 'owner': owner}
