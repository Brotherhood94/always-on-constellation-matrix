"""Journal limited test environments independently from the production workspace."""
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import re
import secrets
import socket
import sys
from subprocess import SubprocessError
import time
from types import SimpleNamespace

from installer import directory, load, operator_lock, run, save

ID = re.compile(r'[a-f0-9]{32}')
PROFILE = re.compile(r'[a-f0-9]{24}')
MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{2,79}')
AGENT = re.compile(r'[a-z0-9][a-z0-9_-]{0,35}')


def job_path(data, key):
    if not isinstance(key, str) or not ID.fullmatch(key):
        raise ValueError('Use the twin ID shown in Control Room.')
    return data / 'twins' / key / 'job.json'


def choices(data):
    from hiring import ensure_coordinator
    snapshot = load(data / 'architecture/snapshot.json')
    accounts = ensure_coordinator(data)['accounts']
    return {'revision': snapshot['revision'], 'agents': list(snapshot['agents'])[:16],
            'accounts': [{'id': row['id'], 'label': row['label'][:64]} for row in accounts[:12] if row['ready']]}


def models(data, account):
    from hiring import control
    if not PROFILE.fullmatch(account):
        raise ValueError('Use a connected account ID.')
    result = control(data, {'action': 'catalog', 'account': account})
    return {'models': [{'id': row['id'], 'efforts': row['efforts']} for row in result.get('models', [])[:16]]}


def setup(data, *, onboarding=False):
    from agent_picker import BACK, checklist, choose_option, terminal_picker
    from installer import workspace_command
    from setup_guide import interactive_terminal
    from shared_provider import catalog

    command = workspace_command(data, elevated=False) + ' twin setup'
    settings = load(data / 'settings.json')
    if not settings.get('control_runtime'):
        raise ValueError('Digital twin setup requires the workspace control runtime.')
    existing = sorted((data / 'twins').glob('*/job.json'))
    for item in existing:
        job = load(item)
        if job['status'] != 'stopped' and not job.get('cleaned'):
            print(f"Digital twin {job['id']} · {job['status']}.")
            return
    path = data / 'twins/setup.json'
    if onboarding and path.is_file() and load(path).get('result') in {'queued', 'skipped'}:
        return
    registry = load(data / 'agents.json')
    snapshot_file = data / 'architecture/snapshot.json'
    if not snapshot_file.is_file() or not registry['agents'].get('the-architect', {}).get('active'):
        if not onboarding:
            raise ValueError('Configure The Architect and refresh the workspace status first.')
        return
    snapshot = load(snapshot_file)
    agents = {key: entry['name'] for key, entry in snapshot['agents'].items() if key != 'the-architect'}
    if not agents:
        if not onboarding:
            raise ValueError('Configure an Office agent to run tests in the twin.')
        return
    if not interactive_terminal():
        raise ValueError('Run ' + command + ' from an interactive terminal.')
    provider_file = data / 'team-provider.json'
    preferred = load(provider_file).get('default') if provider_file.is_file() else None
    while True:
        action = choose_option('🧪 Set up a Digital Twin', {'start': 'Configure a test workspace', 'later': 'Set up later'},
                               default='later', details=(
            'Use a separate workspace on this Docker Engine to test agent workflows with synthetic data.',
            'The twin has its own Matrix account, rooms, storage and temporary provider access. It expires automatically.',
            'Changes tested in the twin are not applied to the main workspace.',
        ))
        if action == 'later':
            if onboarding:
                directory(path.parent)
                save(path, {'result': 'skipped'})
            print('Configure the twin later with ' + command)
            return
        profile = catalog(data, preferred=preferred, back=True)
        if profile is not BACK:
            break
    initial = list(agents)[:3]
    selected = terminal_picker(checklist, '🤖 Select Digital Twin agents', agents, initial, focus_done=True)
    if selected is None:
        print('Available agents: ' + ', '.join(agents))
        entered = input('Twin agent IDs, separated by commas (Enter to use ' + ', '.join(initial) + '): ')
        selected = [key.strip() for key in entered.split(',') if key.strip()] if entered.strip() else initial
    if not 1 <= len(selected) <= 8:
        raise ValueError('Select between one and eight agents for the Digital Twin.')
    durations = {'60': '1 hour', '180': '3 hours', '360': '6 hours'}
    minutes = int(choose_option('⏱ Digital Twin runtime', durations, default='60',
                                details=('The twin automatically stops and expires the test credentials after this period.',)))
    identifier = secrets.token_hex(16)
    plan = {**profile, 'agents': selected, 'minutes': minutes, 'revision': snapshot['revision']}
    directory(path.parent)
    enqueue(data, identifier, plan)
    save(path, {'result': 'queued', 'id': identifier})
    print(f'Digital twin {identifier} queued.')
    print(workspace_command(data, elevated=False) + ' twin status ' + identifier)


def dashboard_snapshot(data, timestamp):
    jobs = sorted((load(path) for path in (data / 'twins').glob('*/job.json')),
                  key=lambda job: job['created'], reverse=True)[:10]
    return {'time': timestamp, 'jobs': [{
        'id': job['id'], 'status': job['status'], 'created': job['created'], 'expires': job.get('expires_at'),
        'agents': job['plan']['agents'], 'model': job['plan']['model'], 'phase': job.get('phase'),
    } for job in jobs]}


def validate(plan):
    if not isinstance(plan.get('account'), str) or not PROFILE.fullmatch(plan['account']):
        raise ValueError('Use a connected account ID.')
    if not isinstance(plan.get('model'), str) or not MODEL.fullmatch(plan['model']):
        raise ValueError('Use a model from the selected connection.')
    if plan.get('effort') not in ('none', 'minimal', 'low', 'medium', 'high', 'xhigh'):
        raise ValueError('Use a supported reasoning effort.')
    agents = plan.get('agents')
    if not isinstance(agents, list) or not 1 <= len(agents) <= 8 or not all(isinstance(key, str) and AGENT.fullmatch(key) for key in agents) or len(agents) != len(set(agents)):
        raise ValueError('Choose one to eight distinct configured agents.')
    overrides = plan.get('overrides', {})
    if not isinstance(overrides, dict) or set(overrides) - set(agents):
        raise ValueError('Each model exception must belong to a selected agent.')
    for profile in overrides.values():
        if not isinstance(profile, dict) or set(profile) != {'model', 'effort'} or not isinstance(profile['model'], str) or not MODEL.fullmatch(profile['model']) or profile['effort'] not in ('none', 'minimal', 'low', 'medium', 'high', 'xhigh'):
            raise ValueError('Use one model and reasoning effort for each override.')
    if type(plan.get('minutes')) is not int or not 15 <= plan['minutes'] <= 360:
        raise ValueError('Use a duration between 15 and 360 minutes.')
    if not isinstance(plan.get('revision'), str) or not re.fullmatch('[a-f0-9]{64}', plan['revision']):
        raise ValueError('Choose the current configuration revision.')


def enqueue(data, identifier, plan):
    validate(plan)
    from hiring import control
    path = job_path(data, identifier)
    with operator_lock(data):
        if path.exists():
            return load(path)
        snapshot = load(data / 'architecture/snapshot.json')
        if snapshot['revision'] != plan['revision'] or any(key not in snapshot['agents'] for key in plan['agents']):
            raise ValueError('The configuration changed. Request a new twin plan.')
        catalog = control(data, {'action': 'catalog', 'account': plan['account']}).get('models', [])
        profiles = [plan, *plan.get('overrides', {}).values()]
        if any(not any(option['id'] == profile['model'] and profile['effort'] in option['efforts']
                       for option in catalog) for profile in profiles):
            raise ValueError('The model and effort were not verified on this account.')
        jobs = map(load, (data / 'twins').glob('*/job.json'))
        if any(job['status'] != 'stopped' and not job.get('cleaned') for job in jobs):
            raise ValueError('Stop the current twin before starting another.')
        directory(path.parent)
        job = {'id': identifier, 'plan': plan, 'snapshot': snapshot, 'status': 'queued', 'created': int(time.time())}
        save(path, job)
        return job


def set_status(path, state, **fields):
    job = load(path)
    job.update(status=state, **fields)
    save(path, job)


def announce(data, key, state):
    identifier = sha256(f'twin:{key}:{state}'.encode()).hexdigest()[:32]
    save(data / 'monitor/outbox' / (identifier + '.json'), {'id': identifier, 'time': time.time(),
         'body': f'Digital twin {key} · {state}. Use ./constellation_matrix twin status {key} for details.'}, mode=0o640)
    os.chown(data / 'monitor/outbox' / (identifier + '.json'), 0, 991)


def port():
    with socket.socket() as selected:
        selected.bind(('127.0.0.1', 0))
        return selected.getsockname()[1]


def start(data, identifier):
    from hiring import control
    from installer import install
    from operation_timing import record
    started = time.monotonic_ns()
    path = job_path(data, identifier)
    job = load(path)
    if job['status'] != 'queued':
        return
    plan = job['plan']
    workspace = path.parent / 'workspace'
    project = 'constellation-twin-' + identifier[:16]
    expiration = int(time.time()) + plan['minutes'] * 60
    set_status(path, 'starting', expires_at=expiration, project=project, grants=[])
    outcome = 'failed'
    phase = 'provider authorization'
    try:
        settings = load(data / 'settings.json')
        rows = control(data, {'action': 'accounts'})['accounts']
        mode = next(row['mode'] for row in rows if row['id'] == plan['account'] and row['ready'])
        proxy = run('docker', 'compose', '--project-directory', str(data), '--profile', 'agents', 'ps', '-q',
                    'hiring-provider', capture_output=True, text=True).stdout.strip()
        address = run('docker', 'inspect', '--format',
                      '{{(index .NetworkSettings.Networks "' + settings['project'] + '_hiring-models").IPAddress}}',
                      proxy, capture_output=True, text=True).stdout.strip()
        import ipaddress
        if not ipaddress.ip_address(address).is_private:
            raise ValueError('Provider route is unavailable.')
        directory(workspace)
        directory(workspace / 'state')
        registry = {'local_server': identifier[:16] + '.twin.invalid', 'agents': {}, 'team_version': 2, 'dynamic_handoffs': True}
        for agent in plan['agents']:
            original = job['snapshot']['agents'][agent]
            profile = plan.get('overrides', {}).get(agent, plan)
            grant = 'tw-' + sha256(f'{identifier}:{agent}'.encode()).hexdigest()[:30]
            token = secrets.token_hex(32)
            current = load(path)
            current['grants'].append(grant)
            save(path, current)
            result = control(data, {'action': 'grant', 'agent': grant, 'account': plan['account'], 'mode': mode,
                'model': profile['model'], 'effort': profile['effort'], 'credential': token, 'expires_at': expiration, 'environment': identifier})
            if result.get('status') != 'granted':
                raise ValueError('Twin model authorization failed.')
            state = workspace / 'state' / agent / 'hiring-agent'
            directory(state, uid=991)
            save(state / 'token', token + '\n', uid=991)
            registry['agents'][agent] = {**original, 'model': profile['model'], 'effort': profile['effort'],
                'active': False, 'server': registry['local_server'], 'placement': 'local', 'hiring_provider': {'mode': mode, 'account': plan['account']},
                'twin_grant': grant, 'starter_role': agent}
        save(workspace / 'agents.json', registry)
        password_file = path.parent / 'operator-password'
        save(password_file, secrets.token_urlsafe(32) + '\n')
        config_path = path.parent / 'workspace.json'
        save(config_path, {'project': project, 'server_name': registry['local_server'], 'owner': 'twin-operator',
            'tailscale': False, 'systemd': False, 'dot': False, 'telegram': False, 'control_runtime': False,
            'agents': plan['agents'], 'owner_password_file': str(password_file), 'matrix_port': port(),
            'context_port': port(), 'neo4j_port': port(), 'bolt_port': port(), 'twin': {'upstream': address,
                'provider_network': settings['project'] + '_hiring-models', 'grants': load(path)['grants'],
                'service_config': {key: value for key, value in job['snapshot']['services'].items()
                                   if key in plan['agents'] or key.removeprefix('engine-') in plan['agents']}}})
        phase = 'workspace creation'
        install(workspace, SimpleNamespace(config=config_path))
        from installer import activate_twin_agent, up
        up(workspace, 'hiring-provider')
        for agent in plan['agents']:
            phase = 'agent readiness for ' + agent
            activate_twin_agent(workspace, agent)
        set_status(path, 'ready', matrix_port=load(workspace / 'settings.json')['matrix_port'])
        outcome = 'completed'
        announce(data, identifier, 'ready')
    except (OSError, ValueError, KeyError, StopIteration, SubprocessError) as error:
        set_status(path, 'failed', error=type(error).__name__, phase=phase)
        announce(data, identifier, 'failed')
    finally:
        record(data / 'runtime/timing', 'twin-start', started, outcome, actor='the-architect', environment=identifier)


def stop(data, identifier):
    from hiring import control
    path = job_path(data, identifier)
    job = load(path)
    workspace = path.parent / 'workspace'
    set_status(path, 'stopping')
    unavailable = []
    for grant in job.get('grants', []):
        try:
            if control(data, {'action': 'revoke', 'agent': grant}).get('status') != 'revoked':
                unavailable.append(grant)
        except (OSError, ValueError, SubprocessError):
            unavailable.append(grant)
    if (workspace / 'compose.yaml').is_file():
        run('docker', 'compose', '--project-directory', str(workspace), '--profile', 'agents', 'down', '--volumes', '--remove-orphans')
    from shutil import rmtree
    if workspace.is_dir():
        rmtree(workspace)
    for name in ('operator-password', 'workspace.json'):
        (path.parent / name).unlink(missing_ok=True)
    if unavailable and time.time() < job.get('expires_at', 0):
        raise ValueError('The provider has not confirmed twin access revocation.')
    terminal = 'failed' if job.get('error') else 'stopped'
    set_status(path, terminal, finished_at=int(time.time()), cleaned=True)
    announce(data, identifier, 'stopped' if terminal == 'stopped' else 'failed and cleaned up')


def command(data, args):
    if args.action == 'setup':
        setup(data)
    elif args.action == 'list':
        for item in sorted((data / 'twins').glob('*/job.json')):
            job = load(item)
            print(job['id'], job['status'])
    elif args.action == 'status':
        job = load(job_path(data, args.id))
        stopping = not job.get('cleaned') and (job_path(data, args.id).parent / 'stop-requested').is_file()
        print('Twin', args.id, 'stopping' if stopping else job['status'])
        if job.get('error'):
            print('Startup check failed', job.get('phase', 'setup'), job['error'])
        print('Revision', job['snapshot']['revision'])
        print('Agents', ', '.join(job['plan']['agents']))
        if job.get('expires_at'):
            print('Expires', datetime.fromtimestamp(job['expires_at'], timezone.utc).isoformat())
        if job.get('matrix_port') and job['status'] == 'ready':
            print('Matrix URL', 'http://127.0.0.1:' + str(job['matrix_port']))
            print('Use an SSH port forward and retrieve the test login with twin access ' + args.id)
    elif args.action == 'access':
        from setup_guide import interactive_terminal
        path = job_path(data, args.id)
        if not interactive_terminal():
            raise ValueError('Retrieve the test password from a private interactive terminal.')
        if load(path)['status'] != 'ready' or (path.parent / 'stop-requested').exists():
            raise ValueError('The twin is not ready for sign-in.')
        print('Matrix operator twin-operator')
        print((path.parent / 'operator-password').read_text().strip())
    else:
        with operator_lock(data):
            path = job_path(data, args.id)
            if (job := load(path))['status'] not in {'stopped', 'stopping'} and not job.get('cleaned'):
                save(path.parent / 'stop-requested', '')


if __name__ == '__main__':
    start(Path(sys.argv[1]), sys.argv[2])
