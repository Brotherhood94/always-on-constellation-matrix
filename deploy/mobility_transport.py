"""Tailnet transport for authenticated worker enrollment, jobs and scoped role calls."""
from contextlib import contextmanager
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
from tempfile import NamedTemporaryFile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener

from installer import KEY, command_output, directory, load, save

LEASE_SECONDS = 60
MAX_CONTROL = 2_000_000
MAX_ARCHIVE = 32 * 1024**3
ID = re.compile(r'[a-f0-9]{32}')
OPERATIONS = {'prepare', 'checkpoint', 'restore', 'verify', 'recover'}
ROLE_CALLS = {
    'manager': {'telegram-office', 'telegram-handoff'},
    'task-manager': {'telegram-office', 'telegram-handoff'},
    'telegram-agent': {'telegram-mcp'},
    'hiring-manager': {'roster'},
    'coach': {'roster'},
    'scouter': {'roster'},
}


def root(data):
    path = data / 'mobility'
    for folder in (path, path / 'hosts', path / 'invites', path / 'jobs', path / 'archives'):
        directory(folder)
    return path


def secret_digest(value):
    return sha256(value.encode()).hexdigest()


def tailscale():
    state = json.loads(command_output('tailscale', 'status', '--json'))
    if state.get('BackendState') != 'Running' or not state.get('Self', {}).get('ID'):
        raise ValueError('Join the running workspace tailnet before using host relocation')
    return state


def hostname(node):
    return node['DNSName'].rstrip('.').lower()


def signed_peer(address):
    node = json.loads(command_output('tailscale', 'whois', '--json', str(ipaddress.ip_address(address))))['Node']
    return str(node.get('StableID') or node.get('ID'))


def tail_ip(node):
    for candidate in node.get('TailscaleIPs', []):
        if ipaddress.ip_address(candidate).version == 4:
            return candidate
    raise ValueError('A Tailscale IPv4 address is required')


def choice_peer(state, target):
    nodes = [node for node in state.get('Peer', {}).values()
             if node.get('DNSName') and hostname(node) == target.lower().rstrip('.')]
    if len(nodes) != 1 or not nodes[0].get('ID') or nodes[0].get('OS') != 'linux':
        raise ValueError('Specify the full Tailscale DNS name of a Linux peer')
    return nodes[0]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


class Channel:
    def __init__(self, config):
        url = urlsplit(config['endpoint'])
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.path not in ('', '/') or url.fragment or url.query:
            raise ValueError('Invalid private relocation endpoint')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=config['ca'])
        self.opener = build_opener(HTTPSHandler(context=context), NoRedirect())
        self.endpoint = config['endpoint'].rstrip('/')
        self.token = config['credential']

    def request(self, method, path, body=None, *, size=None, timeout=30):
        headers = {'Authorization': 'Bearer ' + self.token}
        if body is not None:
            headers['Content-Length'] = str(size)
            headers['Content-Type'] = 'application/octet-stream'
        return self.opener.open(Request(self.endpoint + path, body, headers, method=method), timeout=timeout)

    def json(self, path, payload, *, timeout=30):
        body = json.dumps(payload).encode()
        with self.request('POST', path, body, size=len(body), timeout=timeout) as response:
            raw = response.read(MAX_CONTROL + 1)
        if len(raw) > MAX_CONTROL:
            raise ValueError('The coordinator response exceeds its size limit')
        return json.loads(raw)

    def upload(self, identifier, path):
        with path.open('rb') as stream, self.request('PUT', '/archive/' + identifier, stream,
                size=path.stat().st_size, timeout=180) as response:
            return json.loads(response.read(MAX_CONTROL))

    def download(self, identifier, path):
        with self.request('GET', '/archive/' + identifier, timeout=180) as response, path.open('xb') as stream:
            size = int(response.headers.get('Content-Length', '0'))
            copy_bytes(response, stream, size, MAX_ARCHIVE)


def copy_bytes(src, dst, size, limit):
    if not 0 < size <= limit:
        raise ValueError('Invalid transfer size')
    while size:
        chunk = src.read(min(size, 1024**2))
        if not chunk:
            raise ValueError('The transfer ended early')
        dst.write(chunk)
        size -= len(chunk)


@contextmanager
def transport_lock(path):
    import fcntl
    with (path / 'transport.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def find_host(path, digest):
    for file in (path / 'hosts').glob('*.json'):
        record = load(file)
        if record['credential'] == digest:
            return file, record
    raise PermissionError('Invalid worker credentials')


def enrollment(path, credential, node, worker_token):
    from hmac import compare_digest
    if not isinstance(worker_token, str) or not re.fullmatch(r'[0-9a-f]{64}', worker_token):
        raise ValueError('Invalid host enrollment credential')
    invite_path = path / 'invites' / (credential + '.json')
    digest = secret_digest(worker_token)
    if not invite_path.is_file():
        _, host = find_host(path, digest)
        if host['node'] != node or host.get('enrollment') != credential:
            raise PermissionError('This invitation was used by another worker')
        return {'host': host['host']}
    invite = load(invite_path)
    if invite['node'] != node or invite['expires'] < time.time():
        raise PermissionError('This invitation has expired or belongs to another host')
    receipt = path / 'hosts' / (invite['host'] + '.json')
    if receipt.exists() and not compare_digest(load(receipt)['credential'], digest):
        raise PermissionError('A different worker is already enrolled for this host')
    save(receipt, {'host': invite['host'], 'node': node, 'credential': digest,
                   'enrollment': credential, 'seen': 0, 'agents': {}})
    invite_path.unlink()
    return {'host': invite['host']}


def leased_agents(registry, host):
    agents = {**registry['agents']}
    if registry.get('telegram_placement'):
        agents['telegram-agent'] = registry['telegram_placement']
    return {key: row['mobility_generation'] for key, row in agents.items()
            if row.get('placement') == 'remote' and row.get('server') == host and row.get('active') and
            row.get('mobility_generation') and not row.get('mobility_fenced')}


def staged_agents(registry, host):
    engines = {}
    for move in registry.get('relocations', {}).values():
        phase = move['phase']
        if phase in {'restoring', 'provider-required', 'committing'} and move['destination'] == host:
            engines[move['agent']] = move['target']['generation']
        elif phase in {'rollback-restore', 'rollback-provider', 'rollback-commit'} and move['source'] == host:
            generation = move.get('rollback_target', {}).get('generation')
            if generation:
                engines[move['agent']] = generation
    return engines


def active_report(data, registry, key):
    path = data / 'mobility' / 'hosts'
    entry = registry['agents'].get(key) or registry.get('telegram_placement', {})
    host = entry.get('server')
    if not host or not entry.get('mobility_generation') or not (path / (host + '.json')).is_file():
        return None
    state = load(path / (host + '.json'))
    agent = state.get('agents', {}).get(key, {})
    if agent.get('generation') != entry['mobility_generation']:
        return {'ready': False, 'checked': state.get('seen')}
    checked = state.get('seen', 0)
    return {'ready': bool(agent.get('ready') and time.time() - checked <= LEASE_SECONDS), 'checked': checked}


def poll(data, path, host_path, host, payload):
    jobs = path / 'jobs'
    pending = []
    results = payload.get('completed', {})
    working = payload.get('agents', {})
    adopted = payload.get('adopted', {})
    if not isinstance(results, dict) or not isinstance(working, dict) or not isinstance(adopted, dict):
        raise ValueError('Invalid worker heartbeat')
    for file in jobs.glob('*.json'):
        job = load(file)
        if job['host'] != host['host'] or 'result' in job:
            continue
        taken = results.get(job['id'])
        if type(taken) is dict and type(taken.get('ok')) is bool:
            fields = ('ok', 'code', 'engine', 'ca', 'workshop_token', 'amplifier', 'checkpoint')
            job['result'] = {key: taken[key] for key in fields if key in taken}
            save(file, job)
        else:
            pending.append(job)
    registry = load(data / 'agents.json')
    leases = leased_agents(registry, host['host'])
    host['agents'] = {key: {'generation': leases[key], 'ready': row.get('ready') is True}
                      for key, row in working.items() if key in leases and isinstance(row, dict)
                      and row.get('generation') == leases[key]}
    host['seen'] = int(time.time())
    host['adopted'] = {key: digest for key, digest in adopted.items() if KEY.fullmatch(key) and
                       isinstance(digest, str) and re.fullmatch(r'[a-f0-9]{64}', digest)}
    save(host_path, host)
    snapshots = {}
    if leases:
        for name in ('agents.json', 'integrations.json', 'metrics.json'):
            file = data / 'dashboard' / name
            if file.is_file() and file.stat().st_size <= 350_000:
                snapshots[name] = load(file)
    workshop = {}
    for key in leases.keys() & {'hiring-manager', 'coach', 'scouter'}:
        file = data / 'state' / key / 'matrix/workshop.json'
        if file.is_file():
            workshop[key] = load(file)
    return {'leases': leases, 'staging': staged_agents(registry, host['host']), 'seconds': LEASE_SECONDS,
            'jobs': pending[:1], 'snapshots': snapshots, 'workshop': workshop}


def archive_grant(path, host, identifier, method):
    if not ID.fullmatch(identifier):
        raise PermissionError('Unknown transfer')
    for file in (path / 'jobs').glob('*.json'):
        job = load(file)
        if (job.get('transfer') == identifier and job['host'] == host and 'result' not in job and
            ((method == 'PUT' and job['operation'] in ('checkpoint', 'recover')) or
             (method == 'GET' and job['operation'] == 'restore'))):
            return path / 'archives' / (identifier + '.tar.gz')
    raise PermissionError('This worker has no grant for the transfer')


def relay(data, registry, host, request):
    key, generation, operation, payload = (request.get(field) for field in ('agent', 'generation', 'operation', 'payload'))
    if operation not in ROLE_CALLS.get(key, ()) or not isinstance(payload, dict):
        raise PermissionError('The role cannot call this service')
    if leased_agents(registry, host).get(key) != generation or not generation:
        raise PermissionError("The agent’s activation lease has ended")
    settings = load(data / 'settings.json')
    if operation == 'roster':
        from roster_broker import handle
        payload['actor'] = key
        from installer import read_password_file
        # The role key stays with the broker. Its normal Matrix poll checks still run.
        payload['key'] = read_password_file(data / 'roster' / (key + '.key'), 'Role bridge')
        return handle(data, payload)

    base = data / 'telegram'
    sock = {
        'telegram-office': base / 'office/actions.sock',
        'telegram-handoff': base / 'handoffs/requests.sock',
        'telegram-mcp': base / 'mcp/tools.sock',
    }[operation]
    if not settings['telegram'] or not sock.exists():
        return {'status': 'unavailable'}
    from http.client import HTTPConnection
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(60)
        connection.connect(str(sock))
        http = HTTPConnection('localhost', timeout=60)
        http.sock = connection
        try:
            http.request('POST', '/', json.dumps(payload).encode(), {'Content-Type': 'application/json'})
            reply = http.getresponse().read(MAX_CONTROL + 1)
            if len(reply) > MAX_CONTROL:
                raise ValueError('Invalid service response')
            return json.loads(reply)
        finally:
            http.close()


def serve(data):
    path = root(data)
    config = load(path / 'endpoint.json')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return

        def json_reply(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def body(self):
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= MAX_CONTROL:
                raise ValueError('Unexpected worker request length')
            return json.loads(self.rfile.read(size))

        def call(self):
            self.connection.settimeout(180)
            value = self.headers.get('Authorization', '')
            if not value.startswith('Bearer ') or len(value) > 200:
                raise PermissionError('Authentication required')
            credential = secret_digest(value[7:])
            node = signed_peer(self.client_address[0])
            if self.command == 'POST' and self.path == '/enroll':
                body = self.body()
                with transport_lock(path):
                    enrolled = enrollment(path, credential, node, body.get('credential'))
                self.json_reply(200, enrolled)
                return
            if self.command == 'POST' and self.path == '/rpc':
                request = self.body()
                registry = load(data / 'agents.json')
                key = request.get('agent')
                entry = registry.get('telegram_placement', {}) if key == 'telegram-agent' else registry['agents'].get(key, {})
                rpc = entry.get('mobility_rpc') or {}
                if credential != rpc.get('digest') or node != rpc.get('node'):
                    raise PermissionError('Invalid agent service credential')
                self.json_reply(200, relay(data, registry, entry.get('server'), request))
                return
            host_path, host = find_host(path, credential)
            if host['node'] != node:
                raise PermissionError('Worker credential belongs to another Tailscale device')
            if self.command == 'POST' and self.path == '/poll':
                body = self.body()
                with transport_lock(path):
                    response = poll(data, path, host_path, load(host_path), body)
                self.json_reply(200, response)
                return
            if self.path.startswith('/archive/') and self.command in ('GET', 'PUT'):
                file = archive_grant(path, host['host'], self.path.removeprefix('/archive/'), self.command)
                if self.command == 'PUT':
                    size = int(self.headers.get('Content-Length', '0'))
                    with NamedTemporaryFile(dir=file.parent) as output:
                        temporary = Path(output.name + '.stage')
                        try:
                            copy_bytes(self.rfile, output, size, MAX_ARCHIVE)
                            output.flush()
                            os.fsync(output.fileno())
                            os.link(output.name, temporary)
                            temporary.replace(file)
                        finally:
                            temporary.unlink(missing_ok=True)
                    self.json_reply(200, {'ok': True})
                else:
                    with file.open('rb') as stream:
                        self.send_response(200)
                        self.send_header('Content-Length', str(file.stat().st_size))
                        self.end_headers()
                        shutil.copyfileobj(stream, self.wfile, 1024**2)
                return
            self.json_reply(404, {'error': 'Unknown worker operation'})

        def dispatch(self):
            try:
                self.call()
            except PermissionError:
                self.json_reply(403, {'error': 'This worker is not authorized'})
            except (OSError, ValueError, KeyError, TypeError):
                self.json_reply(409, {'error': 'The worker operation could not be completed'})

        do_POST = do_GET = do_PUT = dispatch

    with ThreadingHTTPServer((config['ip'], config['port']), Handler) as server:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(path / 'server.pem', path / 'server.key')
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        server.serve_forever()
