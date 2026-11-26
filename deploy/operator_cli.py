"""Run setup prompts as the operator and elevate fixed deployment operations."""
from contextlib import contextmanager, nullcontext
import json
from subprocess import SubprocessError
from types import SimpleNamespace
import sys

from installer import available_profiles, starter_profiles
from agent_picker import select_agents
from installer import (CLIENT, PRESETS, SOURCE, agent_activate, install, interactive, load, password,
                       pending_local_agents, prompt_graph_password, request, run, save, show_element_setup,
                       telegram, telegram_layout_options, workspace_command)
from onboarding import confirm_sign_in, deliver_welcome, progress, select_roster
from roster import enable_management
from setup_guide import confirm_element, element_page, interactive_terminal


HOST_ACTIONS = ('metadata', 'install', 'enroll', 'configure', 'finish', 'telegram-layout')
GUIDE_FIELDS = ('server_name', 'owner', 'tailscale', 'telegram', 'dot')
SUDO_PASSWORD_NOTICE = 'If sudo prompts for a password, enter the Linux password for the VPS account shown in the prompt.'


def publish_metadata(data, settings=None, registry=None):
    path = data / 'settings.json'
    if settings is None:
        if not path.is_file():
            return
        settings = load(path)
    if registry is None:
        registry_path = data / 'agents.json'
        registry = load(registry_path) if registry_path.is_file() else {'agents': {}}
    state = progress(data / 'onboarding.json')
    save(data / 'setup.json', {
        'settings': {name: settings[name] for name in GUIDE_FIELDS},
        'matrix_port': settings['matrix_port'],
        'element_confirmed': 'element' in state['confirmed'],
        'select_agents': bool(state.get('select_agents')),
        'selection': state.get('selection'),
        'pending': pending_local_agents(registry),
        'selected': settings['agents'],
        'welcomed': 'welcome' in state,
        'graph_password_configured': (data / 'context/neo4j-password').is_file(),
    }, mode=0o644)


def elevate(data, *args, payload=None):
    options = {'input': json.dumps(payload), 'text': True} if payload is not None else {}
    run('sudo', '--', str(SOURCE / 'constellation_matrix'), '--data-dir', str(data), *args, **options)


def metadata(data, *, refresh_legacy=False):
    path = data / 'setup.json'
    if refresh_legacy and not path.is_file() and (data / 'settings.json').is_file():
        elevate(data, '_host', 'metadata')
    if path.is_file():
        return load(path)
    if (data / 'settings.json').is_file():
        raise ValueError(f'Publish setup information once with {workspace_command(data)} status --refresh')
    raise ValueError(f'Install the workspace first with {workspace_command(data, elevated=False)} install')


def install_as_operator(data, args):
    if args.config:
        elevate(data, 'install', '--config', str(args.config.absolute()))
        return
    current = metadata(data, refresh_legacy=True) if (data / 'settings.json').is_file() else {}
    settings, passphrase = interactive(available_profiles(), current.get('settings', {}))
    graph = None if current.get('graph_password_configured') else prompt_graph_password()
    print(
        '\n🔐 Authorize workspace installation\n'
        'The installer uses sudo to start Docker services, write protected deployment files, '
        'and enable startup at boot.\n'
        f'{SUDO_PASSWORD_NOTICE}\n'
        'Use the Matrix password for Element and the Context Intelligence password for Neo4j Browser.\n',
        flush=True,
    )
    elevate(data, '_host', 'install', payload={'settings': settings, 'password': passphrase, 'graph_password': graph})
    if interactive_terminal():
        setup_as_operator(data, passphrase=passphrase)
    else:
        print(f'Continue setup with {workspace_command(data, elevated=False)} setup-guide')


def print_guide(data, *, root=False):
    if root and not (data / 'setup.json').is_file():
        settings = load(data / 'settings.json')
    else:
        settings = metadata(data)['settings']
    show_element_setup(settings, data, elevated=False)


@contextmanager
def matrix_session(current, passphrase):
    settings = current['settings']
    port = current['matrix_port']
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Invalid local Matrix port in setup information')
    session = request('POST', CLIENT + '/login', {
        'type': 'm.login.password', 'identifier': {'type': 'm.id.user', 'user': settings['owner']},
        'password': passphrase, 'initial_device_display_name': 'Constellation setup (temporary)',
    }, local_port=port)
    token = session['access_token']
    try:
        if session['user_id'] != f"@{settings['owner']}:{settings['server_name']}":
            raise ValueError('Unexpected Matrix operator account')
        yield token
    finally:
        request('POST', CLIENT + '/logout', {}, token, local_port=port)


def setup_as_operator(data, *, passphrase=None):
    current = metadata(data, refresh_legacy=True)
    settings = current['settings']
    command = workspace_command(data, elevated=False)
    confirmed = current['element_confirmed']
    try:
        if not confirmed and confirm_element(*element_page(settings)) is None:
            print(f'\nResume setup with {command} setup-guide')
            return
        if not confirmed or current['select_agents']:
            selection = current['selection']
            if current['select_agents'] and selection is None:
                selection = select_agents(starter_profiles())
            payload = {'selection': selection}
            if selection and passphrase is None:
                passphrase = password(settings)
            session = matrix_session(current, passphrase) if selection else nullcontext()
            with session as token:
                if token is not None:
                    payload['token'] = token
                reason = (
                    'The installer uses sudo to save agent registrations in the protected deployment directory '
                    'and start local Amplifier containers with Docker.' if selection else
                    'The installer uses sudo to save your setup progress in the protected deployment directory.'
                )
                print(f'\n🔐 Authorize agent setup\n{reason}\n{SUDO_PASSWORD_NOTICE}\n', flush=True)
                elevate(data, '_host', 'enroll', payload=payload)
            current = metadata(data)
        configure_as_operator(data, current=current, selection=current['selected'])
    except KeyboardInterrupt:
        print(f'\nResume setup with {command} setup-guide')
    except SubprocessError:
        print(f'Resume setup with {command} setup-guide')
        raise


def configure_as_operator(data, key=None, *, current=None, selection=None):
    if current is None:
        current = metadata(data, refresh_legacy=True)
    pending = current['pending']
    if selection is not None:
        pending = [name for name in pending if name in selection]
    if key is not None:
        elevate(data, '_host', 'configure', key)
    else:
        for number, name in enumerate(pending, start=1):
            print(f'Configuring local agent {number} of {len(pending)}', flush=True)
            elevate(data, '_host', 'configure', name, '--pending-only')
    current = metadata(data)
    if current['welcomed']:
        print('Setup complete.')
    elif current['element_confirmed'] and not current['select_agents'] and not set(current['selected']).intersection(current['pending']):
        elevate(data, '_host', 'finish')


def telegram_as_operator(data, args):
    action, *rest = args
    options = telegram_layout_options(action, rest)
    current = metadata(data, refresh_legacy=True)
    settings = current['settings']
    if not settings['telegram']:
        raise ValueError('Select Telegram in the installer first')
    with matrix_session(current, password(settings, options.owner_password_file)) as token:
        elevate(data, '_host', 'telegram-layout', payload={'action': action, 'token': token,
                                                         'bridge_room': options.bridge_room})


def execute_host(data, args):
    try:
        if args.action in {'install', 'enroll', 'telegram-layout'}:
            payload = json.load(sys.stdin)
        if args.action == 'install':
            install(data, SimpleNamespace(config=None), entered=payload)
        elif args.action == 'enroll':
            selection = payload['selection']
            token = payload.get('token')
            if selection is not None and (not isinstance(selection, list) or
                                          any(not isinstance(key, str) for key in selection)):
                raise ValueError('Provide the selected agent IDs')
            state = progress(data / 'onboarding.json')
            if selection is None and state.get('select_agents') and state.get('selection') is None:
                raise ValueError('Choose the agents to install before registering them')
            if (selection or state.get('selection')) and (not isinstance(token, str) or not token):
                raise ValueError('Sign in with the workspace’s Matrix operator account')
            confirm_sign_in(data)
            select_roster(data, data / 'onboarding.json', None, selection=selection, operator_token=token)
        elif args.action == 'configure':
            if not args.key:
                raise ValueError('Specify the agent to configure')
            agent_activate(data, args.key, pending_only=args.pending_only)
            if args.key in {'chief-of-staff', 'hiring-manager', 'coach', 'scouter'}:
                enable_management(data, automatic=True)
        elif args.action == 'finish':
            enable_management(data, automatic=True)
            deliver_welcome(data, workspace_command(data, elevated=False))
        elif args.action == 'telegram-layout':
            action, token, room = (payload[key] for key in ('action', 'token', 'bridge_room'))
            if action not in ('activate', 'layout') or not isinstance(token, str) or not token:
                raise ValueError('Sign in with the workspace’s Matrix operator account')
            options = [f'--bridge-room={room}'] if room else []
            telegram(data, [action, *options], operator_token=token)
    finally:
        publish_metadata(data)
