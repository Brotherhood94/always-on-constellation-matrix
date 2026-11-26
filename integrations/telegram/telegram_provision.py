from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
from urllib.parse import quote

from bridge_config import database_uri, load, write
from layout import CLIENT, encoded, ensure_room, invite, join, missing, reconcile_direct_account_data, room_route
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


def create_room(alias, name, token, domain, owner, agent, *, space=False, dot=None, expected=None):
    alias_route = '/_matrix/client/v3/directory/room/' + quote(f'#{alias}:{domain}', safe='')
    rooms_route = '/_matrix/client/v3/rooms/'
    try:
        room = request('GET', alias_route)['room_id']
    except MatrixError as error:
        if error.code != 'M_NOT_FOUND':
            raise
        room = None
    restore_alias = expected and not room
    if expected and room and room != expected:
        raise ValueError('A Telegram alias points to a different room than the saved layout.')
    room = room or expected
    if room:
        if membership(room, agent, token) != 'join':
            raise SystemExit('An existing Matrix alias belongs to a different room.')
        route = rooms_route + quote(room, safe='')
        state = read_room_state(route, token)
        if not expected and incomplete_room(state, agent, space):
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
            raise SystemExit('Telegram Agent must remain encrypted.')
        active = {entry['state_key'] for entry in state.values() if entry['type'] == 'm.room.member' and
                  entry['content']['membership'] in ('invite', 'join')}
        if active - {agent, owner, dot}:
            raise SystemExit('An unexpected member is present in Telegram Agent.')
        if ('m.room.tombstone', '') in state:
            raise SystemExit('The configured Telegram Agent room has been replaced.')
        if data.get('m.room.name', {}).get('name') != name:
            request('PUT', route + '/state/m.room.name', {'name': name}, token)
    creation = state['m.room.create', '']
    if creation['sender'] == agent and owner != agent and owner not in creation['content'].get('additional_creators', []):
        levels = data['m.room.power_levels']
        users = levels.setdefault('users', {})
        if owner not in users:
            users[owner] = 100
            request('PUT', route + '/state/m.room.power_levels', levels, token)
    if membership(room, owner, token) not in ('invite', 'join'):
        request('POST', route + '/invite', {'user_id': owner}, token)
    if restore_alias:
        request('PUT', alias_route, {'room_id': room}, token)
    return room


def device_token(root, token_path, domain, device, display):
    agent = f'@telegram-agent:{domain}'
    secret = root / 'matrix-password'
    if not secret.exists():
        write(secret, (secrets.token_hex(32) + '\n').encode())
    if token_path.exists():
        token = token_path.read_text().strip()
    else:
        password = secret.read_text().strip()
        temporary = register('telegram-agent', password)['access_token']
        request('POST', CLIENT + '/logout', {}, temporary)
        session = request('POST', CLIENT + '/login', {
            'type': 'm.login.password', 'identifier': {'type': 'm.id.user', 'user': agent},
            'password': password, 'device_id': device, 'initial_device_display_name': display,
        })
        token = session['access_token']
    who = request('GET', CLIENT + '/account/whoami', token=token)
    if who['user_id'] != agent or who['device_id'] != device:
        raise ValueError('Unexpected Telegram Matrix device.')
    if not token_path.exists():
        write(token_path, (token + '\n').encode(), uid=991)
    return token


def telegram_space(root, domain, owner, token):
    agent = f'@telegram-agent:{domain}'
    path = root / 'layout.json'
    saved = load(path) if path.exists() else {}
    profile = f'{CLIENT}/profile/{encoded(agent)}/displayname'
    if (missing(profile, token) or {}).get('displayname') != 'Telegram Agent':
        request('PUT', profile, {'displayname': 'Telegram Agent'}, token)
    return create_room('telegram', 'Telegram', token, domain, owner, agent, space=True,
                       expected=saved.get('space'))


def bootstrap(root):
    domain, owner = os.environ['MATRIX_SERVER_NAME'], os.environ['MATRIX_OWNER']
    directory = root / 'connector'
    agent = f'@telegram-agent:{domain}'
    device = 'TELEGRAM_CONNECTOR'
    token = device_token(root, directory / 'matrix-token', domain, device, 'Telegram Connector')
    layout = load(root / 'layout.json')
    space, control = layout['space'], layout['bridge']
    out = directory / 'config.json'
    current = load(out) if out.exists() else {}
    model = os.environ.get('TELEGRAM_MODEL', 'gpt-6.2-sol')
    backend_version = (
        '0.22.17:3da5681942d9e9b4c51376e47ec85e69a001623a:'
        f'chatgpt-codex:cae3eaa32e16b139b8e889b8cc477c3f2d9dee31:bundle-snapshot-v1:{model}'
    )
    engine = {'url': 'wss://telegram-engine:19443/api/v1/acp', 'token': '/state/engine-token',
              'ca': '/state/engine-ca.pem', 'workspace': '/instance/workspace',
              'revision': hashlib.sha256(backend_version.encode()).hexdigest()}
    current.update(domain=domain, owner=owner, agent=agent, device=device, matrixUrl='http://synapse:8008',
                   bridgeBot=f'@telegrambot:{domain}', controlRoom=control, bridgeControl=True, space=space, model=model,
                   auditDatabase=database_uri(root, 'matrix_telegram_audit'), engine=engine)
    write(out, current, uid=991)


def space_child(space, room, token, domain, *, preserve_removal=False):
    route = f'/_matrix/client/v3/rooms/{quote(space, safe="")}/state/m.space.child/{quote(room, safe="")}'
    desired = {'via': [domain], 'suggested': False}
    current = missing(route, token)
    if current is None or (not preserve_removal and not current.get('via')):
        request('PUT', route, desired, token)


def clear_space_link(room, kind, target, token):
    route = f'{room_route(room)}/state/m.space.{kind}/{encoded(target)}'
    if (missing(route, token) or {}).get('via'):
        request('PUT', route, {}, token)


def classify_telegram_rooms(space, owner, token, portals, *, bridge=None, legacy=None):
    domain = owner.split(':', 1)[1]
    bot = f'@telegrambot:{domain}'
    state = read_room_state(room_route(space), token)
    linked = {room for (kind, room), event in state.items()
              if kind == 'm.space.child' and event['content'].get('via')}
    assignments = {}
    for room, peer in portals:
        if room not in linked:
            continue
        kind, _, value = peer.partition(':')
        if kind in {'user', 'chat', 'channel'} and value.isascii() and value.isdecimal():
            assignments[room] = f'@telegram_{value}:{domain}' if kind == 'user' else bot
    assignments.update({room: None for room in (bridge, legacy) if room})
    reconcile_direct_account_data(owner, assignments, token)


def binding_rooms(root):
    database = root / 'connector/telegram.sqlite3'
    if not database.is_file():
        return []
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='binding'").fetchone()
        return [row[0] for row in connection.execute('SELECT room FROM binding ORDER BY id')] if exists else []


def is_bridge_dm(room, owner, bot, token, *, agent=None, allow_owner_only=False):
    try:
        state = read_room_state(room_route(room), token)
    except MatrixError as error:
        if error.code not in {'M_FORBIDDEN', 'M_NOT_FOUND'}:
            raise
        return False
    data = {kind: event['content'] for (kind, key), event in state.items() if not key}
    active = {user: event['content'].get('membership') for (kind, user), event in state.items()
              if kind == 'm.room.member' and event['content'].get('membership') in {'invite', 'join'}}
    levels = data.get('m.room.power_levels', {})
    user_level = levels.get('users', {}).get(owner, levels.get('users_default', 0))
    event_levels = levels.get('events', {})
    state_level = levels.get('state_default', 50)
    can_manage = (state.get(('m.room.create', ''), {}).get('sender') == owner or
                  user_level >= max(levels.get('invite', 0), *(event_levels.get(kind, state_level)
                  for kind in ('m.room.power_levels', 'm.room.name', 'm.space.parent'))))
    return (data.get('m.room.create', {}).get('m.federate') is False
            and data.get('m.room.join_rules', {}).get('join_rule') == 'invite'
            and data.get('m.room.encryption', {}).get('algorithm') == 'm.megolm.v1.aes-sha2'
            and 'm.room.tombstone' not in data and active.get(owner) == 'join'
            and can_manage and set(active) <= {owner, bot, agent} and (bot in active or allow_owner_only))


def bridge_dm(domain, owner, token, recorded, *, select, active=True):
    bot, agent = f'@telegrambot:{domain}', f'@telegram-agent:{domain}'
    room = recorded
    if not room:
        alias = f'{CLIENT}/directory/room/{encoded(f"#telegram-bridge-dm:{domain}")}'
        room = (missing(alias, token) or {}).get('room_id')
    if not room and active:
        directs = missing(f'{CLIENT}/user/{encoded(owner)}/account_data/m.direct', token) or {}
        candidates = [room for room in dict.fromkeys(directs.get(bot, [])) if is_bridge_dm(room, owner, bot, token)]
        if len(candidates) > 1:
            room = select(candidates)
            if room is None:
                return None
        elif candidates:
            room = candidates[0]
    if not room or not is_bridge_dm(room, owner, bot, token, agent=agent, allow_owner_only=True):
        room = ensure_room(domain, 'telegram-workspace', 'Telegram Bridge', token, pin_user=agent)
    if active:
        invite(room, bot, token)
    invite(room, agent, token)
    return room


def verify_cutover(root, previous, bridge):
    database = root / 'connector/telegram.sqlite3'
    if not previous or previous == bridge or not database.is_file():
        return
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        if (connection.execute("SELECT 1 FROM sqlite_master WHERE name='draft'").fetchone()
                and connection.execute("SELECT 1 FROM draft WHERE state IN ('publishing','matrix_accepted','unknown')").fetchone()):
            raise ValueError('Reconcile unconfirmed Telegram deliveries before moving approvals to Telegram Bridge.')


def reconcile_layout(root, domain, owner, operator, agents, *, office=None, bridge_active, select):
    agent = f'@telegram-agent:{domain}'
    agent_token = device_token(root, root / 'connector/matrix-token', domain, 'TELEGRAM_CONNECTOR', 'Telegram Connector')
    space = telegram_space(root, domain, owner, agent_token)
    path = root / 'layout.json'
    previous = load(path) if path.exists() else {}
    config_path = root / 'connector/config.json'
    config = load(config_path) if config_path.exists() else {}
    old_control = config.get('controlRoom') or previous.get('control')
    bridge = bridge_dm(domain, owner, operator, previous.get('bridge'), select=select, active=bridge_active)
    if bridge is None:
        raise ValueError('Select a Telegram Bridge room to continue the layout repair.')
    verify_cutover(root, old_control, bridge)
    legacy = previous.get('legacy') or (old_control if old_control != bridge else None)
    join(space, owner, operator)
    join(bridge, agent, agent_token)
    route = room_route(bridge) + '/state/'
    levels = request('GET', route + 'm.room.power_levels', token=operator)
    users = levels.setdefault('users', {})
    events = levels.setdefault('events', {})
    before = (users.get(agent), events.get('m.room.pinned_events'))
    users[agent] = max(users.get(agent, 0), 10)
    events['m.room.pinned_events'] = 10
    if before != (users[agent], events['m.room.pinned_events']):
        request('PUT', route + 'm.room.power_levels', levels, operator)
    if (missing(route + 'm.room.name', operator) or {}).get('name') != 'Telegram Bridge':
        request('PUT', route + 'm.room.name', {'name': 'Telegram Bridge'}, operator)
    if office:
        state = read_room_state(room_route(office), operator)
        content = {kind: event['content'] for (kind, key), event in state.items() if not key}
        if (office in {agents, space, bridge, legacy} or content.get('m.room.create', {}).get('m.federate') is not False
                or content.get('m.room.join_rules', {}).get('join_rule') != 'invite'
                or content.get('m.room.encryption', {}).get('algorithm') != 'm.megolm.v1.aes-sha2'
                or 'm.room.tombstone' in content or membership(office, owner, operator) != 'join'):
            raise ValueError('Telegram Agent can only join the private encrypted Office room.')
        invite(office, agent, operator)
        join(office, agent, agent_token)
    guide = root / 'guide'
    device_token(root, guide / 'token', domain, 'TELEGRAM_SETUP', 'Telegram Setup Guide')
    if legacy and legacy != bridge:
        clear_space_link(space, 'child', legacy, agent_token)
        clear_space_link(agents, 'child', legacy, operator)
        clear_space_link(legacy, 'parent', agents, agent_token)
        clear_space_link(legacy, 'parent', space, agent_token)
        source = (missing(room_route(legacy) + '/state/m.room.pinned_events', agent_token) or {}).get('pinned', [])
        receipt_path = guide / 'shortcut-pins.json'
        receipt = load(receipt_path) if receipt_path.exists() else {}
        entry = receipt.get('entries', {}).get('telegram-setup', {})
        pins = {entry.get('event'), *receipt.get('replaced', [])}
        remaining = [event for event in source if event not in pins]
        if remaining != source:
            request('PUT', room_route(legacy) + '/state/m.room.pinned_events', {'pinned': remaining}, agent_token)
        write(guide / 'legacy-room-id', (legacy + '\n').encode(), uid=991)
    if previous.get('bridge') and previous['bridge'] != bridge:
        clear_space_link(space, 'child', previous['bridge'], agent_token)
    write(guide / 'room-id', (bridge + '\n').encode(), uid=991)
    write(guide / 'space-id', (space + '\n').encode(), uid=991)
    for room in dict.fromkeys([bridge, *binding_rooms(root)]):
        space_child(space, room, agent_token, domain, preserve_removal=room != bridge)
        clear_space_link(agents, 'child', room, operator)
    clear_space_link(agents, 'child', space, operator)
    clear_space_link(space, 'parent', agents, agent_token)
    clear_space_link(bridge, 'parent', agents, operator)
    classify_telegram_rooms(space, owner, operator, [], bridge=bridge, legacy=legacy)
    if config:
        config.update(controlRoom=bridge, bridgeControl=True, space=space)
        config.pop('dot', None)
        if legacy and legacy != bridge:
            config['legacyControlRoom'] = legacy
        write(config_path, config, uid=991)
    result = {'space': space, 'control': bridge, 'bridge': bridge, **({'legacy': legacy} if legacy else {})}
    write(path, result)
    return result


def channel_credentials(root):
    directory = root / 'connector'
    token = load(root / 'exchange/credential.json')['token']
    write(directory / 'engine-token', (token + '\n').encode(), uid=991)
    write(directory / 'engine-ca.pem', (root / 'exchange/ca.pem').read_bytes(), uid=991)


def configure_dot(root, dot_state, operation):
    directory = root / 'connector'
    path = directory / 'config.json'
    config = load(path)
    domain, control, owner, agent = (config[key] for key in ('domain', 'controlRoom', 'owner', 'agent'))
    dot = f'@dot:{domain}'
    if owner in (dot, agent) or agent != f'@telegram-agent:{domain}' or config.get('dot', dot) != dot:
        raise SystemExit('Unexpected local identities in the Telegram configuration.')
    if config.get('bridgeControl'):
        destination = dot_state / 'telegram-control.json'
        if operation == 'configure':
            existing = load(destination) if destination.is_file() else {}
            write(destination, {**existing, 'room': control, 'owner': owner, 'local': True}, uid=991)
        elif operation == 'leave':
            destination.unlink(missing_ok=True)
        elif operation != 'join' or not destination.is_file() or not load(destination).get('local'):
            raise SystemExit('Configure the local Dot request channel before enabling Telegram tools.')
        print('Dot uses the local request channel. Telegram Bridge membership is private.')
        return
    token = (directory / 'matrix-token').read_text().strip()
    route = '/_matrix/client/v3/rooms/' + quote(control, safe='')
    state = read_room_state(route, token)
    active = {key: entry['content']['membership'] for (kind, key), entry in state.items()
              if kind == 'm.room.member' and entry['content'].get('membership') in ('invite', 'join')}
    data = {kind: entry['content'] for (kind, key), entry in state.items() if not key}
    if (data.get('m.room.create', {}).get('m.federate') is not False or
            data.get('m.room.join_rules', {}).get('join_rule') != 'invite' or
            data.get('m.room.encryption', {}).get('algorithm') != 'm.megolm.v1.aes-sha2' or
            'm.room.tombstone' in data or active.get(agent) != 'join' or active.get(owner) != 'join' or
            active.keys() - {owner, agent, dot}):
        raise SystemExit('Telegram Agent membership or encryption could not be verified.')

    dot_token = (dot_state / 'matrix-token').read_text().strip()
    identity = request('GET', '/_matrix/client/v3/account/whoami', token=dot_token)
    if identity.get('user_id') != dot or identity.get('device_id') != 'MATRIXDOT':
        raise SystemExit('Unexpected Dot Matrix device.')
    if operation == 'configure':
        config['dot'] = dot
        write(path, config, uid=991)
        write(dot_state / 'telegram-control.json', {'room': control, 'owner': owner}, uid=991)
        print('Dot configuration is staged for the current Telegram Agent room.')
    elif operation == 'join':
        if config.get('dot') != dot or load(dot_state / 'telegram-control.json') != {'room': control, 'owner': owner}:
            raise SystemExit('Configure both connectors before adding Dot.')
        if dot not in active:
            request('POST', route + '/invite', {'user_id': dot}, token)
        if active.get(dot) != 'join':
            joined = request('POST', '/_matrix/client/v3/join/' + quote(control, safe=''), {}, dot_token)
            if joined.get('room_id') != control:
                raise SystemExit('Dot joined an unexpected Matrix room.')
        if membership(control, dot, token) != 'join':
            raise SystemExit('Dot membership could not be verified.')
        print('Dot joined the encrypted Telegram Agent room.')
    elif operation == 'leave':
        if dot in active:
            request('POST', route + '/leave', {}, dot_token)
        config.pop('dot', None)
        write(path, config, uid=991)
        (dot_state / 'telegram-control.json').unlink(missing_ok=True)
        print('Dot left Telegram Agent. Restart both connectors after rolling back the code.')
    else:
        raise SystemExit('Unknown Dot configuration operation.')


if __name__ == '__main__':
    action, state, *arguments = sys.argv[1:]
    root = Path(state)
    if action == 'bootstrap':
        bootstrap(root)
    elif action == 'channel':
        channel_credentials(root)
    elif action == 'dot-control':
        configure_dot(root, Path(arguments[0]), arguments[1])
    elif action == 'add-to-space':
        config = load(root / 'connector/config.json')
        agent_token = (root / 'connector/matrix-token').read_text().strip()
        binding = load(root / 'connector' / arguments[0])
        space_child(config['space'], binding['room'], agent_token, config['domain'])
    else:
        raise SystemExit('Unknown provisioning operation.')
