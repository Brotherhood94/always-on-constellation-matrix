"""Resume the setup wizard and publish one welcome task as the operator."""
from hashlib import sha256
import json
from shutil import rmtree
from subprocess import SubprocessError

from installer import (CLIENT, CONNECTOR, RUNTIME_UID, SOURCE, directory, encoded, load,
                       open_existing, operator_lock, password, read_password_file, request, run, save)
from provision import MatrixError
from setup_guide import WELCOME_INSTRUCTIONS, WELCOME_TITLE, confirm_page, pages


DEVICE = 'CONSTELLATIONWELCOME'


def progress(path):
    return load(path) if path.exists() else {'confirmed': []}


def guide(data, settings, command, passphrase):
    path = data / 'onboarding.json'
    registry = load(data / 'agents.json')['agents']
    if 'welcome' not in progress(path) and any(
            entry['placement'] == 'local' and not entry['active']
            for key, entry in registry.items() if key in settings['agents']):
        print(f'Complete local agent sign-in before opening the wizard.\n  {command} agent configure')
        return

    for key, title, body in pages(settings, data, command):
        if key in progress(path)['confirmed']:
            continue
        if not confirm_page(title, body):
            print(f'\nResume setup with {command} setup-guide')
            return
        with operator_lock(data):
            current = progress(path)
            if key not in current['confirmed']:
                current['confirmed'].append(key)
                save(path, current)

    if 'welcome' not in progress(path) and not confirm_page(WELCOME_TITLE, WELCOME_INSTRUCTIONS, 'Finish'):
        print(f'\nResume setup with {command} setup-guide')
        return
    try:
        result = finish(data, command, passphrase)
    except (OSError, MatrixError, ValueError, SubprocessError) as error:
        print(f'Hello Collaboration could not be confirmed. {error}')
        print(f'Resume setup with {command} setup-guide')
        return
    except KeyboardInterrupt:
        print(f'\nResume setup with {command} setup-guide')
        return

    if result.get('waiting'):
        print(result['waiting'])
        print(f'Resume setup with {command} setup-guide')
    elif result.get('event'):
        print('Hello Collaboration is in Office.')
        print(f"https://matrix.to/#/{encoded(result['room'])}/{encoded(result['event'])}")
    else:
        print('Setup complete. Hello Collaboration was skipped because no Office agent was selected.')


def finish(data, command, passphrase=None):
    with operator_lock(data):
        settings, registry = open_existing(data)
        state_path = data / 'onboarding.json'
        state = progress(state_path)
        session = data / 'welcome-session'
        if 'welcome' in state:
            close_session(session)
            return state['welcome']

        if any(key not in state['confirmed'] for key, _, _ in pages(settings, data, command)):
            return {'waiting': 'Confirm each selected setup page before launching Hello Collaboration.'}

        entries = registry['agents']
        selected = settings['agents']
        pending = [entries.get(key, {}).get('name', key) for key in selected if not entries.get(key, {}).get('active')]
        if pending:
            return {'waiting': 'Waiting for agent setup in Element: ' + ', '.join(pending)}

        rooms = load(data / 'rooms.json')
        room = rooms['office']
        server = settings['server_name']
        ids = {key: f'@{key}:{server}' for key in selected}
        result = {'room': room}
        if selected:
            token = session_token(session, rooms['owner'], settings, passphrase)
            members = request('GET', f'{CLIENT}/rooms/{encoded(room)}/joined_members', token=token)['joined']
            missing = [entries[key]['name'] for key, user in ids.items() if user not in members]
            if missing:
                return {'waiting': 'Waiting for these agents to join Office in Element: ' + ', '.join(missing)}
            office = [dict(id=ids[key], name=entries[key]['name'], role=entries[key]['profile']['role'])
                      for key in selected if key not in {'root', 'chief-of-staff'}
                      and entries[key]['profile']['role'] in {'coordinator', 'worker'}]
            if office:
                office.sort(key=lambda agent: agent['role'] != 'coordinator')
                target, *peers = office
                event = publish(settings, session, token, rooms['owner'], room,
                                target, peers[0] if peers else None)
                result['event'] = event
        state['welcome'] = result
        save(state_path, state)
        close_session(session)
        return result


def session_token(session, owner, settings, passphrase):
    directory(session)
    token_file = session / 'matrix-token'
    if token_file.exists():
        return read_password_file(token_file, 'Welcome Matrix')
    login = request('POST', CLIENT + '/login', {
        'type': 'm.login.password',
        'identifier': {'type': 'm.id.user', 'user': settings['owner']},
        'password': passphrase if passphrase is not None else password(settings),
        'device_id': DEVICE, 'initial_device_display_name': 'Constellation setup (temporary)',
    })
    if login.get('user_id') != owner or login.get('device_id') != DEVICE or not login.get('access_token'):
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
            print('The welcome receipt is saved. Rerun setup-guide to close its temporary Matrix session.')
            return
    except OSError:
        print('The welcome receipt is saved. Rerun setup-guide to close its temporary Matrix session.')
        return
    rmtree(session)


def publish(settings, session, token, owner, room, target, peer):
    crypto = session / 'crypto-state'
    directory(crypto, uid=RUNTIME_UID)
    transaction = sha256(('constellation-welcome-v1:' + room).encode()).hexdigest()
    source = SOURCE / 'agents/connector/welcome.mjs'
    result = run(
        'docker', 'run', '--rm', '--init', '-i', '--read-only', '--tmpfs', '/tmp', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges:true', '--network', settings['project'] + '_clients',
        '-v', f'{crypto}:/welcome', '-v', f'{source}:/app/welcome.mjs:ro',
        CONNECTOR, 'node', '/app/welcome.mjs',
        input=json.dumps({'token': token, 'owner': owner, 'device': DEVICE, 'room': room,
                          'transaction': transaction, 'target': target, 'peer': peer}),
        capture_output=True, text=True, timeout=90,
    )
    event = json.loads(result.stdout).get('event')
    if not isinstance(event, str) or not event.startswith('$'):
        raise ValueError('Matrix did not return a welcome event ID')
    return event
