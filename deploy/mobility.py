"""Operator-controlled, resumable placement changes for the always-on team."""
from copy import deepcopy
from pathlib import Path
from secrets import token_hex, token_urlsafe
import json
import os
import platform
import re
from subprocess import CalledProcessError
import sys
import time
from uuid import uuid4

from agent_picker import choose_option
from installer import (PORTS, RUNTIME_UID, SOURCE, compose, compose_config, directory, load, open_existing,
                       configure_network, operator_lock, publish_inventory, run, save, up)
from mobility_runtime import Runtime, TELEGRAM, checkpoint, desktop_ready
from mobility_transport import Channel, ID, LEASE_SECONDS, active_report, choice_peer, hostname, root, secret_digest, tail_ip, tailscale
from remote import private_json

BLOCKED = {'the-architect', 'root', 'chief-of-staff', 'dot'}
TERMINAL = {'complete', 'rolled-back', 'cancelled'}


def register_parsers(commands, agent):
    action = agent.add_parser('move', help='Move an agent to an enrolled Tailscale host, resume or roll back a move')
    action.add_argument('target', metavar='AGENT|resume|rollback')
    action.add_argument('move_id', nargs='?')
    action.add_argument('--to', help='Destination Tailscale hostname or vps')
    action.add_argument('--yes', action='store_true', help='Confirm the displayed move and provider credential transfer')
    host = commands.add_parser('host', help='Enroll Linux workers for agent relocation').add_subparsers(dest='host_action', required=True)
    invite = host.add_parser('invite')
    invite.add_argument('hostname')
    invite.add_argument('--output', type=Path)
    enroll = host.add_parser('enroll')
    enroll.add_argument('--invite', type=Path, required=True)
    enroll.add_argument('--desktop-mount', type=Path, required=True)
    host.add_parser('configure', help='Complete Amplifier sign-in on this enrolled worker').add_argument('agent')
    adopt = host.add_parser('adopt', help='Park a legacy remote worker for its first managed move')
    adopt.add_argument('agent')
    adopt.add_argument('--legacy-data-dir', type=Path, required=True)


def settings_subset(settings):
    return {key: settings[key] for key in {*PORTS, 'server_name', 'project', 'owner', 'tailscale', 'systemd', 'dot',
            'telegram', 'dashboard_url', 'context_url', 'roster_management', 'helper_hints_timezone',
            'scout_weekly_utc'} if key in settings}


def install_unit(data, config, mode, project):
    from host_services import safe_path, stage_roster_runtime, unit
    if os.environ.get('CONSTELLATION_RUNTIME') == 'compose':
        if mode == 'worker':
            deploy_worker_control(data, config, project)
        return
    if not isinstance(project, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}', project):
        raise ValueError('Invalid deployment service name')
    source = safe_path(stage_roster_runtime(data))
    location = safe_path(data)
    name = project + ('-mobility-worker' if mode == 'worker' else '-mobility')
    after_docker = ' docker.service' if mode == 'worker' else ''
    piece = f'/usr/bin/python3 -B {source}/deploy/mobility.py {location}'
    mount = ('RequiresMountsFor=' + safe_path(Path(config['desktop'])) + '\n') if mode == 'worker' else ''
    ending = f'ExecStopPost={piece} stop-workers\n' if mode == 'worker' else ''
    desc = 'Constellation Tailnet worker' if mode == 'worker' else 'Constellation Tailnet placement coordinator'
    unit(name + '.service', f'''[Unit]
Description={desc}
After=network-online.target tailscaled.service{after_docker}
Wants=network-online.target
{mount}Requires=tailscaled.service{after_docker}
[Service]
Type=simple
UMask=0077
ExecStart={piece} {mode}
{ending}Restart=on-failure
RestartSec=4
TimeoutStopSec=150
[Install]
WantedBy=multi-user.target
''')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', name + '.service')


def deploy_worker_control(data, config, project):
    from operator_runtime import image
    binary = os.environ.get('CONSTELLATION_TAILSCALE_BIN')
    if not binary:
        raise ValueError('The enrolled worker requires access to its Tailscale service.')
    mounts = [(SOURCE, SOURCE, 'ro'), (data, data, 'rw'),
              (config['desktop'], config['desktop'], 'rw'),
              ('/var/run/docker.sock', '/var/run/docker.sock', 'rw'),
              ('/var/run/tailscale/tailscaled.sock', '/var/run/tailscale/tailscaled.sock', 'rw'),
              (binary, '/usr/bin/tailscale', 'ro')]
    manifest = data / 'worker-control.yaml'
    save(manifest, {'name': project + '-worker-control', 'services': {'worker': {
        'image': image(), 'restart': 'unless-stopped', 'init': True, 'network_mode': 'host',
        'environment': {'CONSTELLATION_RUNTIME': 'compose'},
        'volumes': [f'{source}:{target}:{mode}' for source, target, mode in mounts],
        'command': ['python3', '-B', str(SOURCE / 'deploy/mobility.py'), str(data), 'worker'],
    }}})
    run('docker', 'compose', '-f', str(manifest), 'up', '-d')


def server_endpoint(data, settings, state):
    location = root(data)
    path = location / 'endpoint.json'
    if path.is_file():
        result = load(path)
        if result['ip'] != tail_ip(state['Self']):
            raise ValueError('Restore the coordinator’s enrolled Tailscale device before using relocation')
        return result
    ip = tail_ip(state['Self'])
    port = 18486
    if port in (settings.get(key) for key in PORTS):
        raise ValueError('TCP port 18486 is reserved for the private relocation coordinator')
    run('openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-nodes', '-days', '3650',
        '-subj', '/CN=Constellation worker transport', '-addext', f'subjectAltName=IP:{ip}',
        '-keyout', str(location / 'server.key'), '-out', str(location / 'server.pem'), capture_output=True)
    result = {'ip': ip, 'port': port, 'endpoint': f'https://{ip}:{port}',
              'ca': (location / 'server.pem').read_text()}
    save(path, result)
    try:
        install_unit(data, result, 'serve', settings['project'])
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return result


def invite_host(data, args):
    with operator_lock(data):
        settings, _ = open_existing(data)
        if not settings['tailscale'] or not (settings['systemd'] or settings.get('control_runtime')):
            raise ValueError('Host enrollment requires Tailscale and the VPS systemd deployment')
        nodes = tailscale()
        peer = choice_peer(nodes, args.hostname)
        host = hostname(peer)
        if len(host) > 253 or not re.fullmatch(r'[a-z0-9][a-z0-9.-]*\.ts\.net', host):
            raise ValueError('Use the enrolled peer’s Tailscale DNS name')
        endpoint = server_endpoint(data, settings, nodes)
        location = root(data)
        if (location / 'hosts' / (host + '.json')).exists():
            raise ValueError('This host already has a worker enrollment')
        credential = token_urlsafe(32)
        output = args.output or Path.cwd() / (host.split('.')[0] + '-constellation-invite.json')
        if output.exists() or output.is_symlink():
            raise ValueError('Choose a new invitation filename')
        save(location / 'invites' / (secret_digest(credential) + '.json'),
             {'host': host, 'node': str(peer['ID']), 'expires': time.time() + 600})
        save(output, {**endpoint, 'credential': credential, 'node': str(peer['ID']), 'host': host,
                      'coordinator': settings['server_name'], 'project': settings['project']})
        if os.environ.get('SUDO_UID', '').isdigit():
            os.chown(output, int(os.environ['SUDO_UID']), int(os.environ['SUDO_GID']))
    print(f'Copy {output} privately to {host}. The invitation expires in ten minutes.')


def enroll_host(data, args):
    if platform.system() != 'Linux' or (os.environ.get('CONSTELLATION_RUNTIME') != 'compose'
                                         and not Path('/run/systemd/system').is_dir()):
        raise ValueError('Enroll a Linux host or VM running systemd and standard Docker')
    if (data / 'settings.json').exists():
        raise ValueError('Choose a worker data directory separate from the coordinator')
    invite = private_json(args.invite)
    here = tailscale()['Self']
    if str(here['ID']) != invite['node'] or hostname(here) != invite['host']:
        raise ValueError('This invitation belongs to a different Tailscale device')
    mount = args.desktop_mount.resolve(strict=True)
    desktop_ready(mount, invite['coordinator'], verify_access=True)
    run('docker', 'compose', 'version', capture_output=True)
    options = json.loads(run('docker', 'info', '--format', '{{json .SecurityOptions}}',
                             capture_output=True, text=True).stdout)
    if any(option.startswith('name=rootless') for option in options or []):
        raise ValueError('Use standard Docker on the enrolled worker')
    directory(data, 0o711)
    with operator_lock(data):
        path = data / 'worker.json'
        if path.exists():
            worker = load(path)
            if any(worker[key] != invite[key] for key in ('host', 'node', 'endpoint', 'coordinator')):
                raise ValueError('This data directory belongs to another workspace')
        else:
            worker = {**invite, 'credential': token_hex(32), 'desktop': str(mount)}
            save(path, worker)
        if Channel(invite).json('/enroll', {'credential': worker['credential']})['host'] != worker['host']:
            raise ValueError('The coordinator returned a different worker identity')
        install_unit(data, worker, 'worker', worker['project'])
    print(f"Enrolled {worker['host']} for agent relocation. Worker data is in {data}.")


def host_command(data, args):
    if args.host_action == 'invite':
        invite_host(data, args)
    elif args.host_action == 'enroll':
        enroll_host(data, args)
    elif args.host_action == 'configure':
        from mobility_worker import configure_worker
        configure_worker(data, args.agent)
    else:
        from mobility_worker import adopt
        adopt(data, args.agent, args.legacy_data_dir)


def select_host(data, settings, source=None, target=None):
    hosts = {}
    for file in (root(data) / 'hosts').glob('*.json'):
        item = load(file)
        if time.time() - item.get('seen', 0) <= LEASE_SECONDS and item['host'] != source:
            hosts[item['host']] = item['host']
    server = settings['server_name']
    if source != server:
        hosts[server] = server + ' (VPS)'
    if target:
        target = server if target.lower() == 'vps' else target.lower().rstrip('.')
        if target not in hosts:
            raise ValueError('Choose a different enrolled host that is online')
        return target
    if not hosts:
        raise ValueError('Enroll an online destination with host invite and host enroll')
    if not sys.stdin.isatty():
        raise ValueError('Specify the destination with --to')
    return choose_option('Choose a Tailscale destination', hosts)


def telegram_entry(data, settings, registry):
    path = data / 'telegram/connector/config.json'
    if not settings['telegram'] or not path.is_file() or not (path.parent / 'backend-verified').is_file():
        raise ValueError('Configure and start Telegram Agent before moving it')
    entry = registry.setdefault('telegram_placement', {
        'server': settings['server_name'], 'name': 'Telegram Agent', 'placement': 'local',
        'model': load(path).get('model', 'gpt-6.2-sol'), 'active': True})
    return entry


def get_entry(data, settings, registry, key):
    if key in BLOCKED:
        raise ValueError('This agent’s host-specific services must stay on the VPS')
    if key == TELEGRAM:
        return telegram_entry(data, settings, registry)
    entry = registry['agents'].get(key)
    if not entry:
        raise ValueError('Select a registered Office agent')
    if entry.get('retirement'):
        raise ValueError('Finish the pending retirement before moving this agent')
    return entry


def current_move(registry, identifier):
    return registry.get('relocations', {}).get(identifier)


def port_for(registry, host, key):
    rows = {**registry['agents'], **({'telegram-agent': registry['telegram_placement']} if registry.get('telegram_placement') else {})}
    busy = {row.get('mobility_port') for entry, row in rows.items() if entry != key and row['server'] == host}
    for op in registry.get('relocations', {}).values():
        if op['destination'] == host and op['agent'] != key and op['phase'] not in TERMINAL:
            busy.add(op['target'].get('port'))
    return next(port for port in range(19445, 20445) if port not in busy)


def payload(data, settings, registry, agent, entry, host, identifier, generation):
    config = {}
    if host != settings['server_name']:
        location = root(data)
        endpoint = load(location / 'endpoint.json')
        node = load(location / 'hosts' / (host + '.json'))
        config.update(endpoint=endpoint['endpoint'], ca=endpoint['ca'], rpc_token=token_hex(32), node=node['node'],
                      port=port_for(registry, host, agent))
        if agent != TELEGRAM:
            config['context_token'] = (data / 'context' / (agent + '.token')).read_text().strip()
        if agent == 'helper':
            from installer import publish_helper_guides
            publish_helper_guides(data)
            config['guides'] = load(data / 'support/guides.json')
    server = settings['server_name']
    return {**config, 'key': agent, 'entry': {field: entry[field] for field in ('name', 'model', 'effort', 'profile', 'starter_role', 'hiring_provider') if field in entry},
            'settings': settings_subset(settings), 'team_version': registry.get('team_version', 1),
            'project_key': secret_digest(server)[:8], 'matrix_url': 'https://' + server,
            'context_url': f"https://{server}:{settings['context_https_port']}", 'transaction': identifier, 'generation': generation}


def create_move(data, key, target, yes=False, *, initial=False):
    with operator_lock(data):
        settings, registry = open_existing(data)
        if not settings['tailscale']:
            raise ValueError('Enable the private Tailscale deployment before moving agents')
        entry = get_entry(data, settings, registry, key)
        if entry.get('mobility_move'):
            raise ValueError('Resume the existing move ' + entry['mobility_move'])
        source = entry['server']
        destination = select_host(data, settings, source=source, target=target)
        legacy_device = None
        if entry['placement'] == 'remote' and not entry.get('mobility_generation'):
            token = (data / 'state' / key / 'matrix/matrix-token').read_text().strip()
            legacy_device = secret_digest(token)
            host = root(data) / 'hosts' / (source + '.json')
            if not host.is_file() or load(host).get('adopted', {}).get(key) != legacy_device:
                raise ValueError('Enroll the existing remote host and run host adopt on it before moving this agent.')
        provider = ('The managed ChatGPT connection stays on the coordinator.' if entry.get('hiring_provider') else
                    'The agent’s saved provider login will transfer.')
        print(f"{entry['name']}: {source} → {destination}. {provider}")
        if not yes and (not sys.stdin.isatty() or input('Type move to confirm: ').strip().lower() != 'move'):
            raise ValueError('Confirm the move in an interactive terminal, or use --yes')
        if key == TELEGRAM or source == settings['server_name'] and entry['active']:
            runtime = Runtime(data, {'key': key, 'settings': settings}, local=True)
            if key == TELEGRAM:
                runtime.check_drain_support(runtime.original, ['connector'])
                runtime.check_telegram_deliveries()
            else:
                runtime.check_drain_support(runtime.source_command, runtime.connectors)
        identifier = uuid4().hex
        old = payload(data, settings, registry, key, entry, source, identifier, entry.get('mobility_generation') or identifier)
        if legacy_device:
            old['legacy_device'] = legacy_device
        if entry.get('mobility_port'):
            old['port'] = entry['mobility_port']
        candidate = payload(data, settings, registry, key, entry, destination, identifier, identifier)
        move = {'id': identifier, 'agent': key, 'source': source, 'destination': destination, 'origin': old, 'target': candidate,
                'phase': 'preparing', 'created': int(time.time()), 'initial': initial,
                'original': {field: entry[field] for field in ('server', 'placement', 'active', 'amplifier', 'mobility_generation',
                             'mobility_rpc', 'mobility_port') if field in entry}}
        registry.setdefault('relocations', {})[identifier] = move
        entry['mobility_move'] = identifier
        save(data / 'agents.json', registry)
        configure_network(settings, data)
    return identifier


def change(data, identifier, phase, **fields):
    with operator_lock(data):
        settings, registry = open_existing(data)
        move = current_move(registry, identifier)
        move.update(fields, phase=phase)
        save(data / 'agents.json', registry)
        return move


def load_move(data, identifier):
    if not isinstance(identifier, str) or not ID.fullmatch(identifier):
        raise ValueError('Use the move ID printed by agent move')
    move = current_move(load(data / 'agents.json'), identifier)
    if not move:
        raise ValueError('Unknown agent move')
    return move


def archive_path(data, transfer):
    return root(data) / 'archives' / (transfer + '.tar.gz')


def remote_job(data, move, label, host, details, operation, transfer=None):
    location = root(data)
    # Hold the journal lock while selecting the job id. A retry must await the same job.
    with operator_lock(data):
        registry = load(data / 'agents.json')
        operation_log = current_move(registry, move['id']).setdefault('jobs', {})
        attempt = operation_log.get(label)
        if attempt:
            old = location / 'jobs' / (attempt + '.json')
            if old.is_file() and load(old).get('result', {}).get('ok') is False:
                attempt = None
        if not attempt:
            attempt = uuid4().hex
            operation_log[label] = attempt
            save(data / 'agents.json', registry)
        file = location / 'jobs' / (attempt + '.json')
        if not file.exists():
            save(file, {'id': attempt, 'host': host, 'details': details, 'operation': operation,
                        **({'transfer': transfer} if transfer else {})})
    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        result = load(file).get('result')
        if result is not None:
            return result
        time.sleep(2)
    raise ValueError(f"Waiting for {host}. Resume move {move['id']} when the worker reconnects.")


def local_job(data, details, operation, transfer):
    runtime = Runtime(data, details, local=True)
    directory(runtime.root)
    if operation == 'prepare':
        runtime.preflight()
    elif operation in ('checkpoint', 'recover'):
        if operation == 'recover' and not runtime.restored(details['generation']):
            runtime.fence(source=False, drain=False)
            return {'ok': True, 'checkpoint': False}
        runtime.fence()
        destination = archive_path(data, transfer)
        partial = destination.with_suffix('.partial')
        checkpoint(runtime.state, runtime.key, partial)
        partial.replace(destination)
    elif operation in ('restore', 'verify'):
        if operation == 'restore':
            runtime.install(archive_path(data, transfer))
        try:
            return {'ok': True, **runtime.probe()}
        except CalledProcessError as error:
            if error.returncode != 78:
                raise
            return {'ok': False, 'code': 'provider_sign_in'}
    return {'ok': True}


def job(data, move, *, side, label, operation, transfer=None, details=None):
    origin = side == 'source'
    config = details or (move['origin'] if origin else move['target'])
    host = move[side]
    if host == config['settings']['server_name']:
        return local_job(data, config, operation, transfer)
    return remote_job(data, move, label, host, config, operation, transfer)


def require_job(receipt, move, side):
    if not receipt.get('ok'):
        host = move[side]
        raise ValueError(f"Worker {host} could not complete {receipt.get('code', 'the operation')}. Resume move {move['id']} after checking it.")


def prepare_telegram_engine(data, move, host, details, receipt):
    from installer import run_telegram
    folder = data / 'telegram/connector'
    config = load(folder / 'config.json')
    internal = host == details['settings']['server_name']
    engine = config['engine']
    engine['url'] = 'wss://telegram-engine:19443/api/v1/acp' if internal else f"wss://{host}:{details['port']}/api/v1/acp"
    if internal:
        engine.pop('relocation', None)
        (data / 'telegram/engine-moved').unlink(missing_ok=True)
    else:
        engine['relocation'] = {
            'AGENT_MOBILITY_ENDPOINT': details['endpoint'], 'AGENT_MOBILITY_KEY': TELEGRAM,
            'AGENT_MOBILITY_GENERATION': details['generation'], 'NODE_EXTRA_CA_CERTS': '/mobility/ca.pem',
        }
        save(data / 'telegram/engine-moved', host + '\n')
    save(folder / 'engine-token', receipt['engine'] + '\n', uid=RUNTIME_UID)
    save(folder / 'engine-ca.pem', receipt['ca'], uid=RUNTIME_UID)
    save(folder / 'config.json', config, uid=RUNTIME_UID)
    run_telegram(data, 'probe')


def provider_required(data, move, dest, rollback=False):
    local = dest == move['target']['settings']['server_name']
    if local:
        command = f"./constellation_matrix agent move resume {move['id']}"
    else:
        command = (f"On {dest}, use the enrolled worker data directory with "
                   f"sudo ./constellation_matrix --data-dir WORKER_DATA host configure {move['agent']}, "
                   f"then resume {move['id']} on the VPS")
    change(data, move['id'], 'rollback-provider' if rollback else 'provider-required')
    raise ValueError('Amplifier needs sign-in. ' + command)


def configure_local(data, details):
    if not sys.stdin.isatty():
        raise ValueError('Resume the move from an interactive terminal for Amplifier sign-in')
    runtime = Runtime(data, details, local=True)
    runtime.warm()
    app = (('/opt/amplifier-acp/.venv/bin/python', '-B', '/opt/telegram/sign_in.py') if details['key'] == TELEGRAM else
           ('/opt/amplifier-cli/.venv/bin/python', '-B', '/opt/office/provider_setup.py'))
    runtime.command('exec', runtime.engine, *app)


def drain_telegram(data, move):
    if move['agent'] == TELEGRAM:
        runtime = Runtime(data, move['origin'], local=True)
        runtime.wait_drain(runtime.original)
        runtime.check_telegram_deliveries()


def publish_placement(data, move, host, details, receipt, *, rollback=False):
    from installer import sync_workshop
    with operator_lock(data):
        settings, registry = open_existing(data)
        current = current_move(registry, move['id'])
        entry = get_entry(data, settings, registry, move['agent'])
        local = host == settings['server_name']
        if local:
            Runtime(data, details, local=True).warm()
        entry.update(server=host, placement='local' if local else 'remote', active=True,
                     mobility_generation=details['generation'])
        if 'amplifier' in receipt:
            entry['amplifier'] = receipt['amplifier']
        entry.pop('mobility_fenced', None)
        if local:
            entry.pop('mobility_port', None)
            entry.pop('mobility_rpc', None)
        else:
            entry['mobility_port'] = details['port']
            entry['mobility_rpc'] = {'digest': secret_digest(details['rpc_token']), 'node': details['node']}
        if not local:
            home = data / 'workshop-management'
            directory(home)
            save(home / f"{move['agent']}.json", {'url': f"https://{host}:{details['port']}",
                 'token': receipt['workshop_token'], 'ca_pem': receipt['ca']})
        compose_config(settings, data, registry)
        # Probe Workshop from its own container before assigning execution to the new generation.
        sync_workshop(settings, data, registry, reconnect=move['agent'])
        current.update(phase='rollback-starting' if rollback else 'starting')
        save(data / 'agents.json', registry)
        publish_inventory(settings, data, registry)


def wait_ready(data, move, host, details):
    with operator_lock(data):
        settings, registry = open_existing(data)
        compose_config(settings, data, registry)
    local = host == settings['server_name']
    if local and move['agent'] != TELEGRAM:
        runtime = Runtime(data, details, local=True)
        runtime.mark(False)
        up(data, runtime.engine, *runtime.connectors)
        return
    if not local:
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            report = active_report(data, load(data / 'agents.json'), move['agent'])
            if report and report['ready']:
                break
            time.sleep(3)
        else:
            raise ValueError('Waiting for the destination heartbeat. Resume ' + move['id'])
    if move['agent'] == TELEGRAM:
        from installer import run_telegram
        runtime = Runtime(data, details, local=True)
        runtime.mark(False)
        run_telegram(data, 'restart-connector')


def drop_transfers(data, move):
    for transfer in (move['id'], move.get('rollback_transfer')):
        if transfer:
            archive_path(data, transfer).unlink(missing_ok=True)
    for identifier in move.get('jobs', {}).values():
        (root(data) / 'jobs' / (identifier + '.json')).unlink(missing_ok=True)


def finish(data, move, outcome):
    with operator_lock(data):
        settings, registry = open_existing(data)
        record = current_move(registry, move['id'])
        entry = get_entry(data, settings, registry, move['agent'])
        record.update(phase=outcome, finished=int(time.time()))
        for field in ('origin', 'target', 'receipt', 'rollback_target', 'original'):
            record.pop(field, None)
        entry.pop('mobility_move', None)
        entry.pop('mobility_fenced', None)
        save(data / 'agents.json', registry)
        publish_inventory(settings, data, registry)
    drop_transfers(data, move)
    print(f"Move {move['id']} {outcome}.")


def forward(data, identifier):
    while True:
        move = load_move(data, identifier)
        phase = move['phase']
        if phase in TERMINAL:
            drop_transfers(data, move)
            return
        print(f"Move {identifier} · {phase.replace('-', ' ')}", flush=True)
        if phase == 'preparing':
            require_job(job(data, move, side='destination', label='prepare', operation='prepare'), move, 'destination')
            change(data, identifier, 'draining')
            settings, registry = open_existing(data)
            publish_inventory(settings, data, registry)
        elif phase == 'draining':
            drain_telegram(data, move)
            require_job(job(data, move, side='source', label='checkpoint', operation='checkpoint', transfer=identifier), move, 'source')
            with operator_lock(data):
                settings, registry = open_existing(data)
                get_entry(data, settings, registry, move['agent'])['mobility_fenced'] = True
                current_move(registry, identifier)['phase'] = 'restoring'
                save(data / 'agents.json', registry)
                publish_inventory(settings, data, registry)
        elif phase in ('restoring', 'provider-required'):
            if phase == 'provider-required' and move['destination'] == move['target']['settings']['server_name']:
                configure_local(data, move['target'])
            checking = phase == 'provider-required'
            if not checking and move['destination'] == move['target']['settings']['server_name']:
                change(data, identifier, phase, local_restore_started=True)
            receipt = job(data, move, side='destination', label='verify' if checking else 'restore',
                          operation='verify' if checking else 'restore', transfer=None if checking else identifier)
            if receipt.get('code') == 'provider_sign_in':
                provider_required(data, move, move['destination'])
            require_job(receipt, move, 'destination')
            if move['agent'] == TELEGRAM:
                try:
                    prepare_telegram_engine(data, move, move['destination'], move['target'], receipt)
                except CalledProcessError:
                    provider_required(data, move, move['destination'])
            change(data, identifier, 'committing', receipt=receipt)
        elif phase == 'committing':
            publish_placement(data, move, move['destination'], move['target'], move['receipt'])
        elif phase == 'starting':
            wait_ready(data, move, move['destination'], move['target'])
            finish(data, move, 'complete')
        else:
            raise ValueError('Resume the rollback with agent move rollback ' + identifier)


def rollback_move(data, identifier):
    while True:
        move = load_move(data, identifier)
        phase = move['phase']
        if phase in TERMINAL:
            drop_transfers(data, move)
            return
        if phase == 'preparing':
            finish(data, move, 'cancelled')
            return
        if phase == 'draining':
            # Resolve a possibly interrupted checkpoint before authorizing a restart.
            drain_telegram(data, move)
            require_job(job(data, move, side='source', label='checkpoint', operation='checkpoint', transfer=identifier), move, 'source')
            begin_rollback_restore(data, move, identifier)
        elif phase not in {'rollback-restore', 'rollback-provider', 'rollback-commit', 'rollback-starting'}:
            untouched = ('restore' not in move.get('jobs', {}) and not move.get('local_restore_started'))
            if phase == 'restoring' and untouched:
                transfer = identifier
            else:
                transfer = move.get('rollback_transfer') or uuid4().hex
                if not move.get('rollback_transfer'):
                    change(data, identifier, phase, rollback_transfer=transfer)
                drain_telegram(data, move)
                receipt = job(data, move, side='destination', label='recover', operation='recover', transfer=transfer)
                require_job(receipt, move, 'destination')
                if receipt.get('checkpoint') is False:
                    transfer = identifier
            begin_rollback_restore(data, move, transfer)
        elif phase in {'rollback-restore', 'rollback-provider'}:
            if move['initial']:
                reset_initial(data, move)
                return
            target = move.get('rollback_target') or {**move['origin'], 'generation': uuid4().hex}
            if move.get('rollback_target') is None:
                change(data, identifier, phase, rollback_target=target)
            checking = phase == 'rollback-provider'
            if checking and move['source'] == target['settings']['server_name']:
                configure_local(data, target)
            receipt = job(data, move, side='source', label='rollback-verify' if checking else 'rollback-restore',
                          operation='verify' if checking else 'restore', transfer=None if checking else move['rollback_transfer'],
                          details=target)
            if receipt.get('code') == 'provider_sign_in':
                provider_required(data, move, move['source'], True)
            require_job(receipt, move, 'source')
            if move['agent'] == TELEGRAM:
                try:
                    prepare_telegram_engine(data, move, move['source'], target, receipt)
                except CalledProcessError:
                    provider_required(data, move, move['source'], True)
            change(data, identifier, 'rollback-commit', receipt=receipt)
        elif phase == 'rollback-commit':
            publish_placement(data, move, move['source'], move['rollback_target'], move['receipt'], rollback=True)
        else:
            wait_ready(data, move, move['source'], move['rollback_target'])
            finish(data, move, 'rolled-back')


def begin_rollback_restore(data, move, transfer):
    with operator_lock(data):
        settings, registry = open_existing(data)
        get_entry(data, settings, registry, move['agent'])['mobility_fenced'] = True
        current_move(registry, move['id']).update(phase='rollback-restore', rollback_transfer=transfer)
        save(data / 'agents.json', registry)


def reset_initial(data, move):
    with operator_lock(data):
        settings, registry = open_existing(data)
        entry = get_entry(data, settings, registry, move['agent'])
        entry.update(move['original'])
        for field in ('mobility_generation', 'mobility_rpc', 'mobility_port'):
            if field not in move['original']:
                entry.pop(field, None)
        save(data / 'agents.json', registry)
        compose_config(settings, data, registry)
    finish(data, move, 'rolled-back')


def execute_move(data, args):
    import fcntl
    if args.target in ('resume', 'rollback'):
        identifier = args.move_id
        load_move(data, identifier)
    else:
        if args.move_id:
            raise ValueError('Use agent move AGENT [--to HOST]')
        identifier = create_move(data, args.target, args.to, args.yes)
    print(f'Move ID {identifier}', flush=True)
    execute_id(data, identifier, rollback=args.target == 'rollback')


def execute_id(data, identifier, *, rollback=False):
    import fcntl
    with (root(data) / (identifier + '.lock')).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('The move is already being processed') from error
        move = load_move(data, identifier)
        if rollback and move['phase'] not in TERMINAL:
            change(data, identifier, move['phase'], rollback_requested=True)
        (rollback_move if rollback or move.get('rollback_requested') else forward)(data, identifier)


if __name__ == '__main__':
    os.umask(0o077)
    data = Path(sys.argv[1])
    mode = sys.argv[2]
    if mode == 'serve':
        from mobility_transport import serve
        serve(data)
    elif mode == 'worker':
        from mobility_worker import supervise
        supervise(data)
    elif mode == 'stop-workers':
        from mobility_worker import stop_workers
        stop_workers(data)
