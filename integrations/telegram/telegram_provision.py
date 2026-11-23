import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
from urllib.parse import quote

from bridge_config import database_uri, load, write
from provision import MatrixError, register, request


def membership(room, name, token):
    try:
        return request('GET', f'/_matrix/client/v3/rooms/{quote(room, safe="")}/state/m.room.member/{quote(name, safe="")}',
                       token=token).get('membership')
    except MatrixError as error:
        if error.code != 'M_NOT_FOUND':
            raise
        return None


def incomplete_room(state, agent, space):
    if state.keys() != {('m.room.create', ''), ('m.room.member', agent)}:
        return False
    creation = state['m.room.create', '']
    expected_type = 'm.space' if space else None
    return (creation['sender'] == agent and creation['content'].get('room_version') == '12'
            and creation['content'].get('m.federate') is False
            and creation['content'].get('type') == expected_type
            and state['m.room.member', agent]['content']['membership'] == 'join')


def read_room_state(route, token):
    return {(event['type'], event['state_key']): event
            for event in request('GET', route + '/state', token=token)}


def create_room(alias, name, token, domain, owner, agent, *, space=False):
    alias_route = '/_matrix/client/v3/directory/room/' + quote(f'#{alias}:{domain}', safe='')
    rooms_route = '/_matrix/client/v3/rooms/'
    try:
        room = request('GET', alias_route)['room_id']
    except MatrixError as error:
        if error.code != 'M_NOT_FOUND':
            raise
        room = None
    if room:
        if membership(room, agent, token) != 'join':
            raise SystemExit('An existing Matrix alias belongs to a different room.')
        route = rooms_route + quote(room, safe='')
        state = read_room_state(route, token)
        if incomplete_room(state, agent, space):
            # Synapse can register the alias before rejecting the initial power levels.
            request('DELETE', alias_route, token=token)
            room = None
    if room is None:
        body = {'name': name, 'room_alias_name': alias, 'preset': 'private_chat', 'visibility': 'private',
                'creation_content': {'m.federate': False}}
        if space:
            body['creation_content']['type'] = 'm.space'
        else:
            body['initial_state'] = [{'type': 'm.room.encryption', 'state_key': '',
                                      'content': {'algorithm': 'm.megolm.v1.aes-sha2'}}]
        room = request('POST', '/_matrix/client/v3/createRoom', body, token)['room_id']
        route = rooms_route + quote(room, safe='')
        state = read_room_state(route, token)
    data = {event_type: event['content'] for (event_type, key), event in state.items() if key == ''}
    if data.get('m.room.create', {}).get('m.federate') is not False or data.get('m.room.join_rules', {}).get('join_rule') != 'invite':
        raise SystemExit('Telegram operator rooms must remain invite-only and non-federated.')
    if space and data['m.room.create'].get('type') != 'm.space':
        raise SystemExit('Telegram alias does not point to a Space.')
    if not space:
        if data.get('m.room.encryption', {}).get('algorithm') != 'm.megolm.v1.aes-sha2':
            raise SystemExit('Telegram Control must remain encrypted.')
        active = {entry['state_key'] for entry in state.values() if entry['type'] == 'm.room.member' and
                  entry['content']['membership'] in ('invite', 'join')}
        if active - {agent, owner}:
            raise SystemExit('Telegram Control must contain only the operator and connector.')
    creation = state['m.room.create', '']
    if creation['sender'] == agent and owner != agent and owner not in creation['content'].get('additional_creators', []):
        levels = data['m.room.power_levels']
        users = levels.setdefault('users', {})
        if owner not in users:
            users[owner] = 100
            request('PUT', route + '/state/m.room.power_levels', levels, token)
    if membership(room, owner, token) not in ('invite', 'join'):
        request('POST', route + '/invite', {'user_id': owner}, token)
    return room


def bootstrap(root):
    domain, owner = os.environ['MATRIX_SERVER_NAME'], os.environ['MATRIX_OWNER']
    directory = root / 'connector'
    agent = f'@telegram-agent:{domain}'
    device = 'TELEGRAM_CONNECTOR'
    secret = root / 'matrix-password'
    if not secret.exists():
        write(secret, (secrets.token_hex(32) + '\n').encode())
    password = secret.read_text().strip()
    token_path = directory / 'matrix-token'
    if token_path.exists():
        token = token_path.read_text().strip()
    else:
        temporary = register('telegram-agent', password)['access_token']
        request('POST', '/_matrix/client/v3/logout', {}, temporary)
        session = request('POST', '/_matrix/client/v3/login', {
            'type': 'm.login.password', 'identifier': {'type': 'm.id.user', 'user': agent},
            'password': password, 'device_id': device, 'initial_device_display_name': 'Telegram Connector',
        })
        token = session['access_token']
        who = request('GET', '/_matrix/client/v3/account/whoami', token=token)
        if who['user_id'] != agent or who['device_id'] != device:
            raise SystemExit('Unexpected Matrix connector identity.')
        write(token_path, (token + '\n').encode(), uid=991)
    control = create_room('telegram-control', 'Telegram Control', token, domain, owner, agent)
    space = create_room('telegram', 'Telegram', token, domain, owner, agent, space=True)
    space_child(space, control, token, domain)
    out = directory / 'config.json'
    current = load(out) if out.exists() else {}
    if current.get('controlRoom', control) != control or current.get('space', space) != space:
        raise SystemExit('The Telegram operator rooms changed. Reconcile the connector before replacing the configuration.')
    model = os.environ.get('TELEGRAM_MODEL', 'gpt-6.2-sol')
    backend_version = (
        '0.22.17:3da5681942d9e9b4c51376e47ec85e69a001623a:'
        f'chatgpt-codex:cae3eaa32e16b139b8e889b8cc477c3f2d9dee31:bundle-snapshot-v1:{model}'
    )
    engine = {'url': 'wss://telegram-engine:19443/api/v1/acp', 'token': '/state/engine-token',
              'ca': '/state/engine-ca.pem', 'workspace': '/instance/workspace',
              'revision': hashlib.sha256(backend_version.encode()).hexdigest()}
    current.update(domain=domain, owner=owner, agent=agent, device=device, matrixUrl='http://synapse:8008',
                   bridgeBot=f'@telegrambot:{domain}', controlRoom=control, space=space, model=model,
                   auditDatabase=database_uri(root, 'matrix_telegram_audit'), engine=engine)
    write(out, current, uid=991)
    print('Telegram and Telegram Control are ready. Accept both invitations in Element.')


def space_child(space, room, token, domain):
    route = f'/_matrix/client/v3/rooms/{quote(space, safe="")}/state/m.space.child/{quote(room, safe="")}'
    request('PUT', route, {'via': [domain], 'suggested': False}, token)


def channel_credentials(root):
    directory = root / 'connector'
    token = load(root / 'exchange/credential.json')['token']
    write(directory / 'engine-token', (token + '\n').encode(), uid=991)
    write(directory / 'engine-ca.pem', (root / 'exchange/ca.pem').read_bytes(), uid=991)


if __name__ == '__main__':
    action, state, *arguments = sys.argv[1:]
    root = Path(state)
    if action == 'bootstrap':
        bootstrap(root)
    elif action == 'channel':
        channel_credentials(root)
    elif action == 'add-to-space':
        config = load(root / 'connector/config.json')
        agent_token = (root / 'connector/matrix-token').read_text().strip()
        binding = load(root / 'connector' / arguments[0])
        space_child(config['space'], binding['room'], agent_token, config['domain'])
    else:
        raise SystemExit('Unknown provisioning operation.')
