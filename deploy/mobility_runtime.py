"""Checkpoint a stopped agent and recreate its services on an enrolled host."""
from contextlib import closing
from copy import deepcopy
from pathlib import Path, PurePosixPath
import json
import os
import shutil
import socket
import sqlite3
from subprocess import CalledProcessError
import tarfile
from tempfile import TemporaryDirectory
import time
from urllib.request import urlopen

from compose import SOURCE, definition, health, volume
from installer import RUNTIME_UID, directory, prepare_images, run, save
from mobility_transport import MAX_ARCHIVE

COMPONENTS = ('matrix', 'dm', 'work', 'instance', 'exchange', 'tool-credentials')
TELEGRAM = 'telegram-agent'
TELEGRAM_IMAGE = 'matrix-telegram-engine:0.1.2'
FENCE_FILES = {'relocation-drain', 'relocation-drained', 'backup-lock.sqlite3',
               'backup-lock.sqlite3-shm', 'backup-lock.sqlite3-wal'}


def parts(key):
    return ('instance', 'exchange') if key == TELEGRAM else COMPONENTS


def settled_name(raw, key):
    path = PurePosixPath(raw)
    if (path.is_absolute() or not path.parts or '..' in path.parts or path.parts[0] not in parts(key) or
            path.name in FENCE_FILES or path.parts[:2] == ('work', 'desktop')):
        raise ValueError('The checkpoint contains a path outside the agent’s private state')
    return path


def checkpoint(state, key, output):
    def include(member):
        path = PurePosixPath(member.name)
        if path.parts[:2] == ('work', 'desktop') or path.name in FENCE_FILES:
            return None
        settled_name(member.name, key)
        if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
            raise ValueError('Remove active sockets or special files from the agent workspace before retrying')
        return member

    with tarfile.open(output, 'w:gz') as archive:
        for name in parts(key):
            item = state / name
            if item.is_dir() and not item.is_symlink():
                archive.add(item, name, filter=include)
    output.chmod(0o600)
    with output.open('rb') as saved:
        os.fsync(saved.fileno())


def restore(archive_file, state, key, transaction):
    """Validate before writing. A replay uses the same staging directory and never overwrites a live agent."""
    with TemporaryDirectory(dir=state.parent, prefix='restore-' + transaction + '-') as temporary:
        target = Path(temporary)
        with tarfile.open(archive_file, 'r:gz') as archive:
            members = {}
            expanded_size = 0
            for member in archive:
                name = settled_name(member.name, key)
                if name in members or not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
                    raise ValueError('An unexpected entry was found in the checkpoint')
                if len(name.parts) == 1 and not member.isdir():
                    raise ValueError('Agent state components must be directories')
                expanded_size += member.size
                if expanded_size > MAX_ARCHIVE:
                    raise ValueError('The expanded checkpoint exceeds the transfer limit')
                members[name] = member
            for name, member in members.items():
                if any(parent in members and not members[parent].isdir() for parent in name.parents):
                    raise ValueError('Checkpoint entries must not traverse a link')
                if member.issym():
                    link = PurePosixPath(member.linkname)
                    resolved = (target / name.parent / link).resolve()
                    if link.is_absolute() or not resolved.is_relative_to(target / name.parts[0]):
                        raise ValueError('A workspace symlink points outside the transferred component')
                if member.islnk():
                    linked = settled_name(member.linkname, key)
                    if linked.parts[0] != name.parts[0] or linked not in members or not members[linked].isfile():
                        raise ValueError('A checkpoint hardlink must reference a regular file in the same state component')
            for name, member in members.items():
                location = target / name
                if member.isdir():
                    location.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    location.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as source, location.open('xb') as output:
                        shutil.copyfileobj(source, output, 1024**2)
            for name, member in members.items():
                location = target / name
                if member.issym():
                    location.parent.mkdir(parents=True, exist_ok=True)
                    location.symlink_to(member.linkname)
                    os.lchown(location, RUNTIME_UID, RUNTIME_UID)
                    continue
                if member.islnk():
                    location.parent.mkdir(parents=True, exist_ok=True)
                    os.link(target / member.linkname, location)
                location.chmod(member.mode & 0o700)
                os.chown(location, RUNTIME_UID, RUNTIME_UID)
        for name in parts(key):
            previous = state / name
            incoming = target / name
            if not incoming.exists():
                if name in ('matrix', 'work', 'instance', 'exchange', 'tool-credentials') and key != TELEGRAM:
                    directory(previous, uid=RUNTIME_UID)
                continue
            if previous.exists():
                previous.rename(target / ('previous-' + name))
            incoming.rename(previous)


def desktop_ready(mount, coordinator, *, verify_access=False):
    if not mount.is_dir() or not os.path.ismount(mount):
        raise ValueError('Mount the coordinator’s restricted SFTP desktop on the worker')
    result = json.loads(run('findmnt', '--json', '--mountpoint', str(mount), '--output', 'SOURCE,FSTYPE',
                            capture_output=True, text=True, timeout=10).stdout)
    expected = 'desktop-share@' + coordinator + ':/desktop'
    filesystems = result.get('filesystems', [])
    if (len(filesystems) != 1 or filesystems[0]['fstype'] not in ('fuse.sshfs', 'sshfs') or
            filesystems[0]['source'].removeprefix('sshfs#') != expected):
        raise ValueError('The desktop mount must be the workspace’s restricted SFTP share')
    if verify_access:
        run('setpriv', '--reuid', str(RUNTIME_UID), '--regid', str(RUNTIME_UID), '--clear-groups',
            'test', '-w', str(mount), timeout=10)


def layered_definition(host, details, runtime, *, local):
    key = details['key']
    settings = details['settings']
    if key == TELEGRAM:
        instance = {'image': TELEGRAM_IMAGE, 'restart': 'no', 'init': True, 'read_only': True, 'tmpfs': ['/tmp'],
                    'user': '991:991', 'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
                    'environment': {'TELEGRAM_MODEL': details['entry']['model']},
                    'volumes': [volume(runtime.state / 'instance', '/instance'), volume(runtime.state / 'exchange', '/exchange')],
                    'healthcheck': health('curl', '-fsS', '--cacert', '/exchange/ca.pem', 'https://telegram-engine:19443/')}
        if local:
            instance['volumes'].append(volume(runtime.state / 'mcp', '/telegram-mcp', True))
            instance['networks'] = ['engine', 'provider', 'workshop']
            return {'name': settings['project'] + '-telegram', 'services': {runtime.engine: instance},
                    'networks': {'engine': {'internal': True}, 'provider': {},
                        'workshop': {'external': True, 'name': settings['project'] + '_workshop-targets'}}}
        services = {runtime.engine: instance}
    else:
        entry = {**details['entry'], 'placement': 'local'}
        registry = {'agents': {key: entry}, 'team_version': details['team_version']}
        main = definition(settings, runtime.data, registry, {'POSTGRES_PASSWORD': ''})
        services = {name: deepcopy(main['services'][name]) for name in (runtime.engine, key, 'helper-dm') if name in main['services']}
        if local:
            for item in services.values():
                item['restart'] = 'no'
            # These networks belong to the coordinator's normal Compose project.
            return {'name': settings['project'], 'services': services, 'networks': main['networks']}
    project = 'constellation-move-' + details['project_key'] + '-' + key
    instance = services[runtime.engine]
    instance['networks'] = ['acp', 'provider']
    # Tailnet Serve exposes the existing token-gated, TLS-pinned Instance endpoint.
    instance['ports'] = [f"127.0.0.1:{details['port']}:19443"]
    instance['environment']['AGENT_INSTANCE_CERTIFICATE_NAMES'] = json.dumps([runtime.engine, host])
    for name, item in services.items():
        item.pop('profiles', None)
        item.pop('cgroup_parent', None)
        item['restart'] = 'no'
        if name != runtime.engine:
            item['networks'] = ['acp', 'provider']
        env = item.setdefault('environment', {})
        for field, value in {
            'MATRIX_URL': details['matrix_url'], 'CONTEXT_INTELLIGENCE_URL': details['context_url'],
            'AGENT_MOBILITY_ENDPOINT': details['endpoint'], 'AGENT_MOBILITY_KEY': key,
            'AGENT_MOBILITY_GENERATION': details['generation'],
            'NODE_EXTRA_CA_CERTS': '/mobility/ca.pem',
        }.items():
            if name != runtime.engine or key == TELEGRAM:
                env[field] = value
        mounts = []
        for item_mount in item['volumes']:
            place = item_mount['target']
            if place == '/workspace/desktop':
                item_mount = volume(runtime.desktop, place)
            elif place == '/context-token':
                item_mount = volume(runtime.data / 'private/context-token', place, True)
            elif place in ('/telegram-office', '/telegram-handoffs', '/run/office-roster.sock', '/roster-key'):
                continue
            mounts.append(item_mount)
        if name != runtime.engine or key == TELEGRAM:
            mounts.append(volume(runtime.data / 'private', '/mobility', True))
        if name == key and key in ('task-manager', 'manager') and settings['telegram']:
            env['TELEGRAM_OFFICE_SOCKET'] = 'role:telegram-office'
            env['TELEGRAM_HANDOFF_SOCKET'] = 'role:telegram-handoff'
        if name == key and key in ('hiring-manager', 'coach', 'scouter') and settings.get('roster_management'):
            env['ROSTER_MANAGEMENT'] = 'true'
            env['ROSTER_CONFIGURE_COMMAND'] = './constellation_matrix agent configure'
        item['volumes'] = mounts
    return {'name': project, 'services': services, 'networks': {'acp': {'internal': True}, 'provider': {}}}


class Runtime:
    def __init__(self, data, details, *, local, desktop=None, host=None):
        self.data, self.details, self.local = data, details, local
        self.key = details['key']
        self.desktop, self.host = desktop, host
        self.state = data / 'telegram' if local and self.key == TELEGRAM else data / 'state' / self.key
        self.engine = 'telegram-engine' if self.key == TELEGRAM else 'engine-' + self.key
        self.connectors = [] if self.key == TELEGRAM else [self.key, *(['helper-dm'] if self.key == 'helper' else [])]
        self.root = data / 'mobility' / 'runtime' / self.key if local else data
        self.compose = self.root / 'compose.yaml'

    def command(self, *args, **kwargs):
        return run('docker', 'compose', '--project-directory', str(self.data), '-f', str(self.compose),
                   '--profile', 'agents', *args, **kwargs)

    def original(self, *args, **kwargs):
        if not self.local:
            return self.command(*args, **kwargs)
        if self.key != TELEGRAM:
            from installer import compose
            return compose(self.data, '--profile', 'agents', *args, **kwargs)
        return run('docker', 'compose', '--env-file', str(self.data / '.env'), '-p', self.details['settings']['project'] + '-telegram',
                   '-f', str(SOURCE / 'integrations/telegram/compose.yaml'), '--profile', 'agent', *args, **kwargs)

    def source_command(self, *args, **kwargs):
        return (self.command if self.compose.exists() else self.original)(*args, **kwargs)

    def _states(self):
        if self.key == TELEGRAM:
            return [self.state / 'connector'] if self.local else []
        return [self.state / 'matrix', *([self.state / 'dm'] if self.key == 'helper' else [])]

    def preflight(self):
        if not self.local:
            from serve import Route, status
            desktop_ready(self.desktop, self.details['settings']['server_name'], verify_access=True)
            run('docker', 'compose', 'version', capture_output=True)
            port = self.details['port']
            route = Route(port, f'tcp://127.0.0.1:{port}', 'tcp')
            rules = status()
            if rules.get('AllowFunnel', {}).get(f'{self.host}:{port}') or (
                    route.handler(rules, self.host) and not route.matches(rules, self.host)):
                raise ValueError('The destination agent port is in use by another Tailscale rule')
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(('127.0.0.1', port))
            with urlopen(self.details['matrix_url'] + '/_matrix/client/versions', timeout=10) as response:
                if not json.load(response).get('versions'):
                    raise ValueError('Matrix is not reachable from this worker')
            if self.key not in (TELEGRAM, 'helper'):
                with urlopen(self.details['context_url'] + '/status', timeout=10) as response:
                    if not json.load(response).get('neo4j_query_connected'):
                        raise ValueError('Context Intelligence is unavailable from this worker')
        if self.key == TELEGRAM:
            run('docker', 'build', '-t', TELEGRAM_IMAGE, '-f', str(SOURCE / 'integrations/telegram/engine/Dockerfile'), str(SOURCE),
                stdout=None if self.local else shutil.DEVNULL)
            if self.local and not (self.state / 'connector/backend-verified').is_file():
                raise ValueError('Configure Telegram Agent before moving it')
        else:
            prepare_images({'dot': False}, {'agents': {self.key: {'placement': 'local'}}})

    def check_drain_support(self, command, services):
        files = {'connector': '/app/service.mjs', 'helper-dm': '/app/helper-dm.mjs'}
        for name in self.running(command, services):
            try:
                command('exec', '-T', name, 'grep', '-q', 'relocation-drained',
                        files.get(name, '/app/connector.mjs'), timeout=15)
            except CalledProcessError as error:
                raise ValueError('The source connector needs this release before it can drain safely') from error

    def fences(self):
        return [folder / 'relocation-drain' for folder in self._states()]

    def restored(self, generation):
        marker = self.root / 'restored'
        return marker.is_file() and marker.read_text().strip() == generation

    def mark(self, moving):
        for file in self.fences():
            if file.parent.exists():
                if moving:
                    save(file, self.details['transaction'] + '\n', uid=RUNTIME_UID)
                else:
                    file.unlink(missing_ok=True)
                    (file.parent / 'relocation-drained').unlink(missing_ok=True)

    def running(self, command, names):
        if not names:
            return set()
        result = command('ps', '--services', '--status', 'running', *names, capture_output=True, text=True, timeout=20)
        return set(result.stdout.splitlines())

    def wait_drain(self, command):
        running = self.running(command, ['connector'] if self.key == TELEGRAM else self.connectors)
        if not running or not self.fences():
            return
        self.mark(True)
        deadline = time.monotonic() + 260
        while time.monotonic() < deadline:
            for fence in self.fences():
                name = 'helper-dm' if fence.parent.name == 'dm' else 'connector' if self.key == TELEGRAM else self.key
                if name in running:
                    receipt = fence.parent / 'relocation-drained'
                    if not receipt.is_file() or receipt.read_text().strip() != self.details['transaction']:
                        break
            else:
                return
            time.sleep(2)
        raise ValueError('The active turn did not drain or Telegram delivery is still uncertain')

    def check_telegram_deliveries(self):
        path = self.state / 'connector/telegram.sqlite3'
        if not path.is_file():
            return
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
            pending = db.execute("SELECT 1 FROM draft WHERE state IN ('publishing','matrix_accepted','unknown')").fetchone()
            if pending:
                raise ValueError('Reconcile the uncertain Telegram delivery in Telegram Bridge before resuming the move')

    def fence(self, *, source=True, drain=True):
        if self.local and self.key == TELEGRAM and drain:
            # Encrypted ingress, polls, receipts and bridge credentials deliberately stay on the VPS.
            self.wait_drain(self.original)
        command = self.source_command if source else self.command
        if not source and not self.compose.is_file():
            return
        if drain and self.key != TELEGRAM:
            self.wait_drain(command)
        save(self.root / 'fenced', self.details['generation'] + '\n')
        services = [*self.connectors, self.engine]
        command('stop', '--timeout', '20', *services, timeout=120)
        command('rm', '-f', *services, timeout=120)
        if self.running(command, services):
            raise ValueError('Could not verify that the source containers stopped')

    def install(self, archive):
        directory(self.root)
        directory(self.state.parent)
        directory(self.state)
        marker = self.root / 'restored'
        if not self.restored(self.details['generation']):
            restore(archive, self.state, self.key, self.details['transaction'])
            # The pinned Instance renews the leaf when the configured SAN changes and keeps its CA.
            save(marker, self.details['generation'] + '\n')
        self.render()
        save(self.root / 'runtime.json', self.details)

    def render(self):
        if not self.local:
            directory(self.data / 'dashboard', 0o755)
            directory(self.data / 'private', uid=RUNTIME_UID)
            directory(self.data / 'support', 0o755)
            for name, value in (('context-token', self.details.get('context_token', '')),
                                ('rpc-token', self.details.get('rpc_token', '')),
                                ('ca.pem', self.details['ca'])):
                save(self.data / 'private' / name, value + '\n', uid=RUNTIME_UID)
            if self.key == 'helper':
                save(self.data / 'support/guides.json', self.details['guides'], mode=0o644)
        save(self.compose, layered_definition(self.host, self.details, self, local=self.local))

    def warm(self):
        self.command('up', '-d', '--no-deps', '--wait', '--wait-timeout', '90', self.engine, timeout=120)
        if not self.local:
            port = self.details['port']
            run('tailscale', 'serve', '--bg', f'--tcp={port}', f'tcp://127.0.0.1:{port}', timeout=30)

    def probe(self):
        self.warm()
        if self.key == TELEGRAM:
            # The VPS connector probes the candidate using its own provider session and delivery policy.
            token = json.loads((self.state / 'exchange' / 'credential.json').read_text())['token']
            receipt = {'engine': token, 'ca': (self.state / 'exchange' / 'ca.pem').read_text()}
        else:
            self.command('run', '--rm', '--no-deps', '-T', self.key, 'node', '/app/move-probe.mjs', timeout=220)
            if self.key == 'helper':
                self.command('run', '--rm', '--no-deps', '-T', '--env', 'MOVE_MATRIX_ONLY=true',
                             '--env', 'MOVE_MATRIX_DEVICE=MATRIXHELPERDM', 'helper-dm',
                             'node', '/app/move-probe.mjs', timeout=45)
            result = self.command('exec', '-T', self.engine, '/opt/amplifier-cli/.venv/bin/python', '-B',
                                  '/opt/office/provider_setup.py', 'metadata', capture_output=True, text=True, timeout=30)
            receipt = {'amplifier': json.loads(result.stdout)}
        if not self.local:
            token_path = self.root / 'workshop-token'
            generation_path = self.root / 'workshop-generation'
            if not token_path.is_file() or not generation_path.is_file() or generation_path.read_text().strip() != self.details['generation']:
                raw = self.command('exec', '-T', self.engine, 'agent-instance', '--home', '/instance', 'token', 'create',
                                   '--name', 'Persona Workshop', '--scopes', 'observe,profiles',
                                   '--expires-in', '25920000', '--local', capture_output=True, text=True, timeout=30)
                save(token_path, json.loads(raw.stdout)['token'] + '\n')
                save(generation_path, self.details['generation'] + '\n')
            receipt.update(workshop_token=token_path.read_text().strip(), ca=(self.state / 'exchange' / 'ca.pem').read_text())
        return receipt

    def start(self):
        self.mark(False)
        (self.root / 'fenced').unlink(missing_ok=True)
        if self.connectors:
            active = self.running(self.command, self.connectors)
            if active != set(self.connectors):
                self.command('up', '-d', '--no-deps', *self.connectors, timeout=45)

    def ready(self):
        names = [*self.connectors, self.engine]
        result = self.command('ps', '--format', 'json', *names, capture_output=True, text=True, timeout=15)
        text = result.stdout.strip()
        rows = json.loads(text) if text.startswith('[') else [json.loads(line) for line in text.splitlines()]
        ready = {row['Service'] for row in rows if row.get('Health') == 'healthy' and row.get('State') == 'running'}
        return ready == set(names)
