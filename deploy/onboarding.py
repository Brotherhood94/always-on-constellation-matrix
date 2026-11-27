"""Resume workspace setup, enroll the selected roster, and send one welcome."""
from hashlib import sha256
import json
from random import choice
from shutil import rmtree
from subprocess import SubprocessError
from urllib.parse import parse_qsl, urlsplit

from agent_picker import select_agents
from installer import (CLIENT, CONNECTOR, RUNTIME_UID, SOURCE, agent_sign_in, directory,
                       encoded, load, onboarding_profiles, open_existing, operator_lock, pending_local_agents,
                       provision_selected_agents, read_password_file, request, save, run, telegram,
                       telegram_agent_pending, telegram_agent_selected)
from layout import room_route
from provision import MatrixError
from roster import enable_management
from setup_guide import confirm_element, element_page, interactive_terminal


CHOOSE_AGENTS = object()
DEVICE = 'CONSTELLATIONWELCOME'
SHORTCUTS = {
    'dashboard': ('Open dashboard', 'dashboard_url'),
    'context': ('Open Context Intelligence', 'context_url'),
}


def progress(path):
    return load(path) if path.exists() else {'confirmed': []}


def guide(data, settings, command, passphrase):
    path = data / 'onboarding.json'
    try:
        select_roster(data, path, passphrase)
    except (OSError, MatrixError, ValueError, SubprocessError) as error:
        print(f'Agent registration could not be completed. {error}')
        print(f'Resume setup with {command} setup-guide')
        return
    except KeyboardInterrupt:
        print(f'\nResume setup with {command} setup-guide')
        return

    settings = load(data / 'settings.json')
    selected = settings['agents']
    pending = pending_local_agents(load(data / 'agents.json'))
    telegram_pending = (interactive_terminal() and telegram_agent_selected(settings) and
                        not (data / 'telegram/connector/backend-verified').is_file())
    local = [key for key in selected if key in pending]
    if local or telegram_pending:
        try:
            if settings.get('control_runtime'):
                from shared_provider import configure_team
                configure_team(data, [*local, *(['telegram-agent'] if telegram_pending else [])])
            agent_sign_in(data, selection=selected, telegram_pending=False)
            if telegram_pending:
                from shared_provider import prepare_telegram
                prepare_telegram(data)
        except (KeyboardInterrupt, OSError, ValueError, SubprocessError) as error:
            if not isinstance(error, KeyboardInterrupt):
                print(f'Provider setup could not be completed. {error}')
            print(f'Resume setup with {command} setup-guide')
            return
    if 'element' not in progress(path)['confirmed']:
        if confirm_element(*element_page(settings)) is None:
            print(f'\nResume setup with {command} setup-guide')
            return
        confirm_sign_in(data)
    try:
        enable_management(data, automatic=True)
    except (KeyboardInterrupt, MatrixError, OSError, ValueError, SubprocessError) as error:
        if not isinstance(error, KeyboardInterrupt):
            print(f'Roster management could not be enabled. {error}')
        print(f'Resume setup with {command} setup-guide')
        return
    deliver_welcome(data, command)
    if settings['telegram'] and not telegram_agent_selected(settings) and interactive_terminal():
        telegram(data, ['setup'])


def confirm_sign_in(data):
    path = data / 'onboarding.json'
    with operator_lock(data):
        current = progress(path)
        if 'element' not in current['confirmed']:
            current['confirmed'].append('element')
            save(path, current)


def select_roster(data, path, passphrase, *, selection=CHOOSE_AGENTS, operator_token=None):
    current = progress(path)
    if not current.get('select_agents'):
        return
    selection = current.get('selection', selection)
    if selection is CHOOSE_AGENTS:
        selection = select_agents(onboarding_profiles(load(data / 'settings.json')))
    with operator_lock(data):
        current = progress(path)
        if not current.get('select_agents'):
            return
        settings, registry = open_existing(data)
        selection = current.setdefault('selection', selection)
        save(path, current)
        authenticated = {'operator_token': operator_token} if operator_token is not None else {}
        provision_selected_agents(data, settings, registry, selection, passphrase, **authenticated)
        current.pop('select_agents')
        current.pop('selection')
        save(path, current)


def deliver_welcome(data, command):
    try:
        result = finish(data)
    except (OSError, MatrixError, ValueError, SubprocessError) as error:
        print(f'The welcome could not be confirmed. {error}')
        print(f'Resume setup with {command} setup-guide')
        return
    except KeyboardInterrupt:
        print(f'\nResume setup with {command} setup-guide')
        return

    if result.get('waiting'):
        print(result['waiting'])
        print(f'Resume setup with {command} setup-guide')
    elif result.get('event'):
        print('Your welcome in Element')
        print(matrix_link(result['room'], result['event']))
    else:
        print('Setup complete. The welcome was skipped because no eligible agent was selected.')


def matrix_link(room, event):
    return f'https://matrix.to/#/{encoded(room)}/{encoded(event)}'


def office_agent(key, entry):
    return key not in {'chief-of-staff', 'root', 'the-architect', 'helper'} and entry['profile']['role'] in {'coordinator', 'worker'}


def matrix_state(data, key):
    return data / 'state' / key / 'matrix'


def finish(data):
    with operator_lock(data):
        settings, registry = open_existing(data)
        state_path = data / 'onboarding.json'
        state = progress(state_path)
        session = data / 'agent-welcome-session'
        legacy = data / 'welcome-session'
        if 'welcome' in state:
            close_session(legacy)
            close_session(session)
            return state['welcome']

        if state.get('select_agents') or 'element' not in state['confirmed']:
            return {'waiting': 'Finish Element sign-in and agent selection before the welcome.'}

        entries = registry['agents']
        selected = settings['agents']
        pending = [entries.get(key, {}).get('name', key) for key in selected if not entries.get(key, {}).get('active')]
        if pending:
            return {'waiting': 'Waiting for agent setup in Element: ' + ', '.join(pending)}
        if (legacy / 'crypto-state').exists():
            return {'waiting': 'An earlier welcome is pending. Resume its delivery with the installer revision that started it.'}
        close_session(legacy)

        office = [key for key in selected if office_agent(key, entries[key])]
        if 'welcome_attempt' in state:
            key = state['welcome_attempt']['agent']
            if key not in selected or (key not in {'chief-of-staff', 'hiring-manager'} and key not in office):
                return {'waiting': 'Restore the selected welcome agent before retrying its delivery.'}
        elif 'hiring-manager' in selected and entries['hiring-manager'].get('starter_role') == 'hiring-manager':
            key = 'hiring-manager'
        elif 'chief-of-staff' in selected:
            key = 'chief-of-staff'
        elif office:
            key = choice(office)
        else:
            state['welcome'] = {'skipped': True}
            save(state_path, state)
            return state['welcome']

        rooms = load(data / 'rooms.json')
        server = settings['server_name']
        agent = f'@{key}:{server}'
        token = read_password_file(matrix_state(data, key) / 'matrix-token', 'Agent Matrix')
        members = request('GET', room_route(rooms['office']) + '/joined_members', token=token)['joined']
        missing = [entries[item]['name'] for item in selected if f'@{item}:{server}' not in members]
        if missing:
            return {'waiting': 'Waiting for these agents to join Office in Element: ' + ', '.join(missing)}
        if rooms['owner'] not in members:
            return {'waiting': 'Join Agents → Office in Element before sending the welcome.'}

        room = rooms['office']
        if key == 'chief-of-staff' or entries[key].get('starter_role') == 'hiring-manager':
            filename = 'control-room-id' if key == 'hiring-manager' else 'room-id'
            room = (matrix_state(data, key) / filename).read_text().strip()
            if room == rooms['office']:
                raise ValueError('The management welcome requires its private chat')
            audience = request('GET', room_route(room) + '/state', token=token)
            participants = {event['state_key']: event['content'].get('membership') for event in audience
                            if event['type'] == 'm.room.member' and event['content'].get('membership') in {'invite', 'join'}}
            rules = next((event['content'] for event in audience if event['type'] == 'm.room.join_rules'), {})
            if rules.get('join_rule') != 'invite' or participants != {agent: 'join', rooms['owner']: 'join'}:
                raise ValueError(f"The management welcome requires the operator and {entries[key]['name']} in their private chat")

        pins = verified_pins(data, settings, rooms['alerts'])
        if not pins:
            return {'waiting': 'Waiting for Open dashboard and Open Context Intelligence in Control Room → Pinned messages.'}

        attempt = {'agent': key, 'sender': agent, 'room': room,
                   'transaction': sha256(f'constellation-agent-welcome:{room}:{agent}'.encode()).hexdigest()}
        if state.get('welcome_attempt', attempt) != attempt:
            raise ValueError('The welcome destination changed during delivery')
        state['welcome_attempt'] = attempt
        save(state_path, state)
        example = next((item for item in office if entries[item]['profile']['role'] == 'coordinator'),
                       office[0] if office else None)
        token = session_token(session, matrix_state(data, key), agent)
        event = publish(settings, session, token, attempt, entries[key]['name'], pins, entries.get(example))
        state['welcome'] = {'sender': agent, 'room': room, 'event': event}
        state.pop('welcome_attempt')
        save(state_path, state)
        close_session(session)
        return state['welcome']


def comparable_url(value):
    url = urlsplit(value)
    return url.scheme, url.netloc.lower().removesuffix(':443'), url.path or '/', parse_qsl(url.query)


def verified_pins(data, settings, alerts):
    try:
        managed = load(data / 'bot/shortcut-pins.json')['entries']
        token = read_password_file(data / 'bot/token', 'Alerts monitor Matrix')
    except FileNotFoundError:
        return None
    path = room_route(alerts)
    try:
        pinned = request('GET', path + '/state/m.room.pinned_events', token=token).get('pinned', [])
        events = {}
        for key, (label, setting) in SHORTCUTS.items():
            entry = managed.get(key, {})
            event = entry.get('event')
            body = entry.get('body', '')
            prefix = label + ' — '
            if (not isinstance(event, str) or not event.startswith('$') or event not in pinned
                    or not body.startswith(prefix)
                    or comparable_url(body.removeprefix(prefix)) != comparable_url(settings[setting])):
                return None
            events[key] = event
        for event in dict.fromkeys(events.values()):
            raw = request('GET', f'{path}/event/{encoded(event)}', token=token)
            if (raw.get('type') != 'm.room.encrypted' or raw.get('sender') != f"@the-architect:{settings['server_name']}"
                    or raw.get('unsigned', {}).get('redacted_because')):
                return None
        return {key: matrix_link(alerts, event) for key, event in events.items()}
    except MatrixError as error:
        if error.code == 'M_NOT_FOUND':
            return None
        raise


def session_token(session, agent_state, sender):
    directory(session)
    token_file = session / 'matrix-token'
    if token_file.exists():
        return read_password_file(token_file, 'Welcome Matrix')
    login = request('POST', CLIENT + '/login', {
        'type': 'm.login.password',
        'identifier': {'type': 'm.id.user', 'user': sender},
        'password': read_password_file(agent_state / 'matrix-password', 'Agent Matrix'),
        'device_id': DEVICE, 'initial_device_display_name': 'Constellation welcome (temporary)',
    })
    if login.get('user_id') != sender or login.get('device_id') != DEVICE or not login.get('access_token'):
        raise ValueError('Matrix returned an unexpected welcome device')
    save(token_file, login['access_token'] + '\n')
    return login['access_token']


def close_session(session):
    token = session / 'matrix-token'
    if not token.is_file():
        return
    try:
        request('POST', CLIENT + '/logout', {}, read_password_file(token, 'Welcome Matrix'))
    except MatrixError as error:
        if error.code != 'M_UNKNOWN_TOKEN':
            print('Rerun setup-guide to close the temporary welcome Matrix session.')
            return
    except OSError:
        print('Rerun setup-guide to close the temporary welcome Matrix session.')
        return
    rmtree(session)


def publish(settings, session, token, attempt, name, pins, example):
    crypto = session / 'crypto-state'
    directory(crypto, uid=RUNTIME_UID)
    source = SOURCE / 'agents/connector/welcome.mjs'
    result = run(
        'docker', 'run', '--rm', '--init', '-i', '--read-only', '--tmpfs', '/tmp', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges:true', '--network', settings['project'] + '_clients',
        '-v', f'{crypto}:/welcome', '-v', f'{source}:/app/welcome.mjs:ro',
        CONNECTOR, 'node', '/app/welcome.mjs',
        input=json.dumps({'token': token, 'device': DEVICE, 'sender': attempt['sender'], 'room': attempt['room'],
                          'transaction': attempt['transaction'], 'name': name, 'pins': pins,
                          'example': {'name': example['name'], 'role': example['profile']['role']} if example else None,
                          'dot': settings['dot']}),
        capture_output=True, text=True, timeout=90,
    )
    event = json.loads(result.stdout).get('event')
    if not isinstance(event, str) or not event.startswith('$'):
        raise ValueError('Matrix did not return a welcome event ID')
    return event
