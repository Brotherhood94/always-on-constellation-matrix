"""Attach a separate-host Constellation worker to an existing Office."""
from hashlib import sha256
import os
from pathlib import Path
import re
import stat

from compose import CONNECTOR, ENGINE, health, volume
from installer import (
    KEY, RUNTIME_UID, SOURCE, configure_network, directory, load, open_existing, operator_lock, run,
    prepare_images, compose, up, publish_inventory, save, validated_url,
)


def private_json(path):
    flags = path.lstat().st_mode
    if not stat.S_ISREG(flags) or flags & 0o077:
        raise ValueError('Enrollment and receipt files must be private regular files')
    return load(path)


def write_new(path, value):
    # Refuse accidental overwrite of another enrollment file.
    if path.exists() or path.is_symlink():
        raise ValueError('Choose a new output filename')
    save(path, value)


def fingerprint(config):
    return sha256(config['matrix_token'].encode()).hexdigest()


def compose_remote(data, config, desktop):
    key = config['key']
    account = config['entry']
    runtime = data / 'state' / key
    worker = {'restart': 'unless-stopped', 'user': '991:991', 'read_only': True, 'init': True,
              'tmpfs': ['/tmp'], 'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true']}
    mounts = [volume(runtime / 'work', '/workspace'), volume(desktop, '/workspace/desktop')]
    metadata = {'AGENT_USERNAME': key, 'AGENT_NAME': account['name'], 'AGENT_MODEL': account['model'],
                'AGENT_REASONING': account['effort']}
    return {'name': 'constellation-remote-' + key, 'services': {
        'engine': {**worker, 'image': ENGINE, 'networks': ['acp', 'outbound'],
                   'environment': {**metadata, 'AGENT_WORKSPACE_TOOLS': 'true',
                                   'AGENT_INSTANCE_CERTIFICATE_NAMES': '["engine"]'},
                   'volumes': [*mounts, volume(runtime / 'instance', '/instance'), volume(runtime / 'exchange', '/exchange')],
                   'healthcheck': health('curl', '-fsS', '--cacert', '/exchange/ca.pem', 'https://engine:19443/')},
        key: {**worker, 'image': CONNECTOR, 'networks': ['acp', 'outbound'],
              'environment': {
                  **metadata, 'MATRIX_SERVER_NAME': config['server_name'], 'MATRIX_URL': config['matrix_url'],
                  'MATRIX_OWNER': config['owner'], 'MATRIX_PLANNER': config.get('planner') or '',
                  'MATRIX_CATALOG_PATH': config['catalog'], 'CONTEXT_INTELLIGENCE_URL': config['context'],
                  'AGENT_STATE': '/state', 'AGENT_WORKSPACE': '/workspace', 'AGENT_INSTRUCTIONS': account['profile']['limits'],
                  'INSTANCE_ACP_URL': 'wss://engine:19443/api/v1/acp'},
              'volumes': [*mounts, volume(runtime / 'matrix', '/state'),
                          volume(runtime / 'exchange', '/acp', True), volume(runtime / 'context-token', '/context-token', True)],
              'depends_on': {'engine': {'condition': 'service_healthy'}},
              'healthcheck': health('test', '-s', '/tmp/matrix-agent-ready')},
    }, 'networks': {'acp': {'internal': True}, 'outbound': {}}}


def share_desktop(settings, data, key_file):
    if not settings['tailscale']:
        raise ValueError('Use an authenticated private tailnet endpoint for desktop SFTP')
    key = key_file.read_text().strip().split()
    if len(key) < 2 or key[0] != 'ssh-ed25519' or not re.fullmatch(r'[A-Za-z0-9+/=]+', key[1]):
        raise ValueError('Provide one Ed25519 public key')
    directory(data / 'desktop-sftp', mode=0o755)
    config = data / 'desktop-sftp'
    host_key = config / 'host-key'
    if not host_key.exists():
        run('ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(host_key))
    authorization = config / 'authorized-keys'
    current = authorization.read_text().splitlines() if authorization.exists() else []
    approved = 'restrict ' + ' '.join(key[:2])
    save(authorization, '\n'.join(dict.fromkeys([*current, approved])) + '\n', mode=0o644)
    save(config / 'sshd_config', (SOURCE / 'deploy/sftp/sshd_config').read_text(), mode=0o644)
    project = settings['project'] + '-desktop'
    definition = {'name': project, 'services': {'desktop-sftp': {
        'build': str(SOURCE / 'deploy/sftp'), 'restart': 'unless-stopped',
        'ports': ['127.0.0.1:18222:2222'],
        'volumes': [volume(config, '/config', True), volume(data / 'desktop', '/chroot/desktop')],
    }}}
    compose_file = data / 'desktop-compose.yaml'
    save(compose_file, definition)
    run('docker', 'compose', '-f', str(compose_file), 'up', '-d', '--build')
    run('docker', 'compose', '-f', str(compose_file), 'exec', '-T', 'desktop-sftp',
        '/usr/sbin/sshd', '-t', '-f', '/config/sshd_config')
    run('tailscale', 'serve', '--bg', '--tcp=2222', 'tcp://127.0.0.1:18222')
    print('Private desktop SFTP is ready. Verify this host public key on the worker:')
    print((config / 'host-key.pub').read_text().strip())


def execute(data, args):
    if args.action == 'desktop':
        with operator_lock(data):
            settings, _ = open_existing(data)
            share_desktop(settings, data, args.public_key)
        return

    if args.action == 'attach':
        config = private_json(args.enrollment)
        mount = args.desktop_mount.resolve(strict=True)
        if not mount.is_dir() or not os.path.ismount(mount):
            raise ValueError('Mount the coordinator desktop over restricted SFTP before starting the worker')
        key = config['key']
        if not KEY.fullmatch(key):
            raise ValueError('Invalid agent ID')
        for field in ('matrix_url', 'catalog', 'context'):
            validated_url(config[field])
        directory(data, 0o711)
        with operator_lock(data):
            state = data / 'state' / key
            for name in ('work/desktop', 'instance', 'exchange', 'matrix'):
                directory(state / name, uid=RUNTIME_UID)
            save(state / 'matrix/matrix-token', config['matrix_token'] + '\n', uid=RUNTIME_UID)
            save(state / 'matrix/room-id', config['room'] + '\n', uid=RUNTIME_UID)
            save(state / 'context-token', config['context_token'] + '\n', uid=RUNTIME_UID)
            save(data / 'compose.yaml', compose_remote(data, config, mount))
            prepare_images({'dot': False}, {'agents': {key: {'placement': 'local'}}})
            up(data, 'engine')
            print('Choose the Amplifier subscription labeled ' + config['entry']['subscription'] + '.')
            compose(data, 'exec', 'engine', '/opt/amplifier-acp/.venv/bin/python', '/opt/telegram/sign_in.py')
            compose(data, 'run', '--rm', '--no-deps', '-T', key, 'node', '/app/probe.mjs')
            up(data, key)
            save(data / 'receipt.json', {'agent': key, 'device': fingerprint(config)})
        print(f'Worker connected. Copy {data / "receipt.json"} to the coordinator for final registration.')
        return

    with operator_lock(data):
        settings, registry = open_existing(data)
        entry = registry['agents'][args.key]
        if entry['placement'] != 'remote' or not settings['tailscale']:
            raise ValueError('The remote profile requires private tailnet connectivity')
        state = data / 'state' / args.key / 'matrix'
        matrix_token = (state / 'matrix-token').read_text().strip()
        if args.action == 'export':
            if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}', args.host):
                raise ValueError('Use a valid remote host label')
            if entry['active'] and entry['server'] != args.host:
                raise ValueError('The active remote worker already has a different host')
            server = settings['server_name']
            entry['server'] = args.host
            write_new(args.output, {
                'key': args.key, 'entry': entry, 'server_name': server, 'matrix_url': 'https://' + server,
                'owner': f"@{settings['owner']}:{server}", 'planner': f'@dot:{server}' if settings['dot'] else None,
                'catalog': settings['dashboard_url'].rstrip('/') + '/agents.json',
                'context': f"https://{server}:{settings['context_https_port']}",
                'room': (state / 'room-id').read_text().strip(), 'matrix_token': matrix_token,
                'context_token': (data / 'context' / f'{args.key}.token').read_text().strip(),
            })
            configure_network(settings, data)
            print('Transfer the enrollment file privately to the authorized worker host.')
        else:
            receipt = private_json(args.receipt)
            if receipt != {'agent': args.key, 'device': fingerprint({'matrix_token': matrix_token})}:
                raise ValueError('The health receipt does not match the enrolled Matrix device')
            entry['active'] = True
        save(data / 'agents.json', registry)
        publish_inventory(settings, data, registry)
