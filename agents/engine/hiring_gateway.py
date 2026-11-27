"""Private Amplifier provider connections for the Office hiring broker."""
import asyncio
import hashlib
import hmac
import inspect
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import re
import secrets
import socketserver
from shutil import copyfile
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import Lock, Thread
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
import zipfile

from amplifier_core import llm_errors
from amplifier_core.message_models import ChatRequest
from amplifier_module_provider_openai_chatgpt import ChatGPTProvider, models, oauth, plan_auth

LOGIN_MODES = {'chatgpt_codex': 'ChatGPT Codex', 'chatgpt_plan': 'ChatGPT Plan'}
API_MODES = {'openai_api': 'OpenAI API', 'anthropic_api': 'Anthropic API'}
MODES = {**LOGIN_MODES, **API_MODES}
EFFORT = {'none', 'minimal', 'low', 'medium', 'high', 'xhigh'}
IDENTIFIER = re.compile(r'[a-f0-9]{24}')
TOKEN = re.compile(r'[a-f0-9]{64}')
MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{2,79}')
AGENT = re.compile(r'[a-z0-9][a-z0-9_-]{0,35}')
MAX_BODY = 1_800_000
ENROLL_SECONDS = 900
HERE = Path(__file__).resolve().parent


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def save(path, value):
    with NamedTemporaryFile(dir=path.parent, mode='w', encoding='utf-8', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            temporary.unlink(missing_ok=True)


def read(path):
    return json.loads(path.read_text())


def key(value, pattern=IDENTIFIER):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError('Invalid identifier')
    return value


def provider_error(error):
    if isinstance(error, llm_errors.AuthenticationError):
        return 'authorization_required'
    if isinstance(error, llm_errors.RateLimitError):
        return 'rate_limited'
    if isinstance(error, (ValueError, llm_errors.InvalidRequestError)):
        return 'invalid_request'
    return 'provider_unavailable'


class LiveCatalogProvider(ChatGPTProvider):
    async def list_models(self, *, filters=None):
        # The pinned provider's public ModelInfo conversion drops the upstream reasoning options.
        await self._ensure_valid_tokens(fresh_from_file=True)
        if self.auth_mode == 'chatgpt_plan':
            rows = await models.fetch_plan_models(access_token=self._tokens['access_token'], strict=True)
        else:
            rows = await models.fetch_models(access_token=self._tokens['access_token'], account_id=self._tokens['account_id'],
                                             client_version=self.models_client_version, strict=True)
        metadata = {row['slug']: row for row in rows}
        return [item.model_copy(update={'defaults': {'reasoning_efforts': metadata[item.id].get(
            'supported_reasoning_levels', metadata[item.id].get('supported_reasoning_efforts', []))}})
            for item in models.to_model_infos(rows, include_speed_variants=False, strict=True)]


def model_choices(model):
    defaults = model.defaults or {}
    advertised = defaults.get('reasoning_efforts', defaults.get('supported_reasoning_efforts', []))
    if isinstance(advertised, dict):
        advertised = advertised.keys()
    elif not isinstance(advertised, list):
        advertised = []
    values = [item.get('effort', item.get('reasoning_effort')) if isinstance(item, dict) else item for item in advertised]
    efforts = list(dict.fromkeys(item for item in values if isinstance(item, str) and item in EFFORT))
    # Omitting the effort uses the selected model's provider default.
    return {'id': model.id, 'efforts': efforts or ['none'], 'model': model.model_dump(mode='json')}


class Connections:
    def __init__(self, state, public_url, *, provider=LiveCatalogProvider, clock=time.time):
        self.state, self.public_url, self.provider, self.clock = state, public_url, provider, clock
        self.connections = state / 'connections'
        self.jobs = state / 'enrollment'
        self.grants = state / 'grants'
        for path in (self.connections, self.jobs, self.grants):
            path.mkdir(mode=0o700, exist_ok=True)
        self.guard = Lock()
        self.locks = {}
        self.running = set()
        self.catalogs = {}

    def locked(self, account):
        with self.guard:
            return self.locks.setdefault(account, Lock())

    def profile(self, account):
        return self.connections / key(account)

    def record(self, account):
        return read(self.profile(account) / 'account.json')

    def accounts(self):
        return [read(path) for path in sorted(self.connections.glob('*/account.json'))]

    def upstream(self, record, *, path=None, model=None, effort=None, hooks=None):
        home = path or self.profile(record['id'])
        config = {'auth_mode': record['mode'], 'token_file_path': str(home / 'oauth.json'),
                  'login_on_mount': False, 'raw': False}
        if model:
            config['default_model'] = model
        if effort and effort != 'none':
            config['reasoning_effort'] = effort
        coordinator = SimpleNamespace(hooks=hooks) if hooks else None
        if record['mode'] in API_MODES:
            from amplifier_module_provider_anthropic import AnthropicProvider
            from amplifier_module_provider_openai import OpenAIProvider
            cls = OpenAIProvider if record['mode'] == 'openai_api' else AnthropicProvider
            model_config = {name: config[name] for name in ('default_model', 'reasoning_effort') if name in config}
            return cls(api_key=(home / 'api-key').read_text().strip(), config=model_config, coordinator=coordinator)
        return self.provider(config, SimpleNamespace(hooks=hooks) if hooks else None)

    async def fetch_models(self, record, *, path=None):
        provider = self.upstream(record, path=path)
        try:
            rows = await asyncio.wait_for(provider.list_models(filters={}), timeout=25)
            return [model_choices(model) for model in rows if MODEL.fullmatch(model.id)]
        finally:
            if (close := getattr(provider, "close", None)) and inspect.isawaitable(pending := close()):
                await pending

    def models(self, account, *, fresh=False):
        with self.locked(account):
            record = self.record(account)
            cached = self.catalogs.get(account)
            if cached and not fresh and self.clock() - cached[0] < 60 and record['ready']:
                return cached[1]
            try:
                models = asyncio.run(self.fetch_models(record))
            except Exception as error:
                self.update_status(record, provider_error(error))
                raise
            self.update_status(record)
            self.catalogs[account] = (self.clock(), models)
            return models

    def update_status(self, record, error=None):
        ready = False if error == 'authorization_required' else True if error is None else record['ready']
        if ready != record['ready']:
            record['ready'] = ready
            save(self.profile(record['id']) / 'account.json', record)

    def enrollment(self, identifier):
        return self.jobs / (key(identifier) + '.json')

    def begin(self, identifier, mode, account=None):
        if mode not in LOGIN_MODES or (account and self.record(account)['mode'] != mode):
            raise ValueError('Invalid ChatGPT mode')
        path = self.enrollment(identifier)
        with self.guard:
            if path.is_file():
                return {'id': identifier}
            if mode == 'chatgpt_plan' and not self.public_url:
                raise ValueError('Publish the private HTTPS hiring enrollment route first.')
            for old in self.jobs.glob('*.json'):
                if read(old)['expires'] < self.clock() - ENROLL_SECONDS:
                    old.unlink(missing_ok=True)
            if sum(read(item)['status'] == 'pending' and read(item)['expires'] > self.clock()
                   for item in self.jobs.glob('*.json')) >= 8:
                raise ValueError('Too many pending connections')
            job = {'id': identifier, 'account': account or secrets.token_hex(12), 'mode': mode,
                   'expires': self.clock() + ENROLL_SECONDS, 'status': 'pending'}
            if mode == 'chatgpt_plan':
                # Only the opaque, expiring pairing value is exposed to the operator.
                job['ticket'] = secrets.token_hex(32)
            save(path, job)
            if mode == 'chatgpt_codex':
                self.running.add(identifier)
                Thread(target=self.codex_login, args=(path, job), daemon=True).start()
        return {'id': identifier}

    def status(self, identifier):
        job = read(self.enrollment(identifier))
        if job['status'] == 'ready':
            return {'status': 'ready', 'account': self.record(job['account'])}
        if job['expires'] < self.clock() or job['status'] == 'failed' or (
                job['mode'] == 'chatgpt_codex' and identifier not in self.running):
            return {'status': 'failed'}
        if job['mode'] == 'chatgpt_plan':
            return {'status': 'pending', 'url': self.public_url + '/desktop/' + job['id'] + '/' + job['ticket']}
        return {key: job[key] for key in ('status', 'url', 'code') if key in job}

    def codex_login(self, path, job):
        def prompt(line):
            line = re.sub(r'\x1b\[[\d;]*m', '', line)
            url = re.search(r'https://auth\.openai\.com/codex/device\b', line)
            code = re.search(r'\b[A-Z0-9]{3,6}-[A-Z0-9]{3,6}\b', line)
            if url:
                job['url'] = url.group()
            if code:
                job['code'] = code.group()
            save(path, job)
        try:
            with TemporaryDirectory(dir=self.state) as folder:
                candidate = Path(folder)
                asyncio.run(oauth.login(token_file_path=str(candidate / 'oauth.json'), print_fn=prompt))
                self.commit_connection(job, candidate)
            save(path, {'status': 'ready', 'mode': job['mode'], 'account': job['account'], 'expires': job['expires']})
        except Exception:
            save(path, {'status': 'failed', 'mode': job['mode'], 'account': job['account'], 'expires': job['expires']})
        finally:
            with self.guard:
                self.running.discard(job['id'])

    def pairing(self, identifier, ticket):
        job = read(self.enrollment(identifier))
        if (job['mode'] != 'chatgpt_plan' or job['status'] != 'pending' or job['expires'] < self.clock()
                or not hmac.compare_digest(key(ticket, TOKEN), job['ticket'])):
            raise PermissionError('Pairing has expired')
        return job

    def import_plan(self, identifier, ticket, credentials):
        with self.locked('enrollment:' + identifier):
            job = self.pairing(identifier, ticket)
            with TemporaryDirectory(dir=self.state) as folder:
                candidate = Path(folder)
                source = candidate / 'import.json'
                host = self.profile(job['account']) / 'host.json'
                if host.is_file():
                    copyfile(host, candidate / 'host.json')
                save(source, credentials)
                asyncio.run(plan_auth.import_credentials(str(source), token_file_path=str(candidate / 'oauth.json'),
                                                         host_file_path=str(candidate / 'host.json')))
                self.commit_connection(job, candidate)
            save(self.enrollment(identifier), {'status': 'ready', 'mode': job['mode'], 'account': job['account'],
                                              'expires': job['expires']})
        return {'status': 'ready'}

    def commit_connection(self, job, candidate):
        account, mode = job['account'], job['mode']
        with self.locked(account):
            if job['expires'] < self.clock():
                raise ValueError('Pairing has expired')
            directory = self.profile(account)
            previous = self.record(account) if (directory / 'account.json').is_file() else None
            credentials = read(candidate / 'oauth.json')
            if mode == 'chatgpt_plan':
                details = plan_auth.auth_status(str(candidate / 'oauth.json'))
                subject, name = details['subject'], details.get('email')
                if not details['plan_enabled']:
                    raise ValueError('ChatGPT plan usage consent was declined')
            else:
                subject, name = credentials.get('account_id'), None
            if not isinstance(subject, str) or not subject or (previous and previous['identity'] != digest(subject)):
                raise ValueError('Connect the original ChatGPT account')
            label = name if isinstance(name, str) and name.isprintable() and len(name) < 90 else MODES[mode] + ' · ' + account[-6:]
            record = {'id': account, 'mode': mode, 'label': label, 'identity': digest(subject), 'ready': True}
            models = asyncio.run(self.fetch_models(record, path=candidate))
            if not models:
                raise ValueError('The account has no eligible models')
            directory.mkdir(mode=0o700, exist_ok=True)
            if mode == 'chatgpt_plan' and not (directory / 'host.json').is_file():
                (candidate / 'host.json').replace(directory / 'host.json')
            (candidate / 'oauth.json').replace(directory / 'oauth.json')
            save(directory / 'account.json', record)
            self.catalogs[account] = (self.clock(), models)

    def connect_key(self, mode, secret):
        if mode not in API_MODES or not isinstance(secret, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,512}', secret):
            raise ValueError('Invalid provider API key.')
        identity = digest(secret)
        with self.guard:
            for record in self.accounts():
                if record['mode'] == mode and record['identity'] == identity:
                    return {'status': 'ready', 'account': {key: value for key, value in record.items() if key != 'identity'}}
            account = secrets.token_hex(12)
            record = {'id': account, 'mode': mode, 'ready': True, 'identity': identity,
                      'label': API_MODES[mode] + ' · ' + account[-6:]}
            with TemporaryDirectory(dir=self.state) as temp:
                draft = Path(temp) / 'profile'
                draft.mkdir(mode=0o700)
                (draft / 'api-key').touch(mode=0o600)
                (draft / 'api-key').write_text(secret)
                catalog = asyncio.run(self.fetch_models(record, path=draft))
                if not catalog:
                    raise ValueError('The account returned no available models.')
                save(draft / 'account.json', record)
                draft.rename(self.profile(account))
            self.catalogs[account] = (self.clock(), catalog)
            return {'status': 'ready', 'account': {key: value for key, value in record.items() if key != 'identity'}}

    def public_accounts(self):
        return [{field: record[field] for field in ('id', 'mode', 'label', 'ready')} for record in self.accounts()]

    def grant(self, payload):
        agent, account = key(payload['agent'], AGENT), key(payload['account'])
        model, effort = key(payload['model'], MODEL), payload['effort']
        credential = key(payload['credential'], TOKEN)
        record = self.record(account)
        if record['mode'] != payload['mode'] or not record['ready']:
            raise llm_errors.AuthenticationError('Selected provider is not connected')
        if not any(row['id'] == model and effort in row['efforts'] for row in self.models(account)):
            raise ValueError('The selected model or reasoning effort is no longer available')
        file = self.grants / (agent + '.json')
        wanted = {'account': account, 'model': model, 'effort': effort, 'credential': digest(credential)}
        if 'expires_at' in payload:
            expiry = payload['expires_at']
            if not agent.startswith('tw-') or type(expiry) is not int or not self.clock() < expiry <= self.clock() + 21600:
                raise ValueError('Invalid twin grant expiration.')
            wanted['expires_at'] = expiry
            if 'environment' in payload:
                wanted['environment'] = key(payload['environment'], re.compile(r'[a-f0-9]{32}'))
        if file.exists() and read(file) != wanted:
            raise ValueError('This agent already has a different provider grant')
        save(file, wanted)
        return {'status': 'granted'}

    def generation(self, agent, authorization, payload, emit, *, relayed=False):
        watch = time.monotonic_ns()
        file = self.grants / (key(agent, AGENT) + '.json')
        grant = read(file)
        if grant.get('expires_at', float('inf')) <= self.clock():
            raise PermissionError('Provider access has expired')
        if not relayed and not hmac.compare_digest(digest(key(authorization, TOKEN)), grant['credential']):
            raise PermissionError('Invalid agent credential')
        account = grant['account']
        if payload.get('catalog') is True:
            if not file.is_file() or read(file) != grant:
                raise PermissionError('Provider access has ended')
            emit({'result': [item['model'] for item in self.models(account) if item['id'] == grant['model']]})
            return
        request = ChatRequest.model_validate(payload['request'])
        # Transport provenance and billing profile are fixed by the approved grant.
        request = request.model_copy(update={'model': grant['model'],
                                             'reasoning_effort': None if grant['effort'] == 'none' else grant['effort']})
        class Hooks:
            async def emit(self, event, data):
                if event in {'llm:stream_block_start', 'llm:stream_block_delta', 'llm:stream_block_end'}:
                    emit({'event': event, 'data': data})

        async def complete():
            provider = self.upstream(record, model=grant['model'], effort=grant['effort'], hooks=Hooks())
            try:
                return await provider.complete(request)
            finally:
                if (close := getattr(provider, 'close', None)) and inspect.isawaitable(pending := close()):
                    await pending

        # One credential owner serializes inference and catalog calls for this account.
        with self.locked(account):
            if not file.is_file() or read(file) != grant or grant.get('expires_at', float('inf')) <= self.clock():
                raise PermissionError('Provider access has ended')
            record = self.record(account)
            if not record['ready']:
                raise llm_errors.AuthenticationError('The connected ChatGPT account needs reauthorization')
            queue = round((time.monotonic_ns() - watch) / 1_000_000)
            outcome = 'completed'
            try:
                reply = asyncio.run(complete())
            except Exception as error:
                outcome = provider_error(error)
                self.update_status(record, outcome)
                raise
            finally:
                if path := os.environ.get('OPERATION_TIMING_OUTBOX'):
                    from operation_timing import record as record_timing
                    record_timing(Path(path), 'provider-inference', watch, outcome, actor=agent, queue_ms=queue,
                                  environment=grant.get('environment'))
            self.update_status(record)
            emit({'result': reply.model_dump(mode='json')})

    def admin(self, payload, emit):
        action = payload['action']
        if action == 'accounts':
            return {'accounts': self.public_accounts()}
        if action == 'catalog':
            return {'models': self.models(payload['account'])}
        if action == 'connect-key':
            return self.connect_key(payload['mode'], payload['key'])
        if action == 'begin':
            return self.begin(payload['id'], payload['mode'], payload.get('account'))
        if action == 'login-status':
            status = self.status(payload['id'])
            if 'account' in status:
                status['account'].pop('identity', None)
            return status
        if action == 'grant':
            return self.grant(payload)
        if action == 'revoke':
            (self.grants / (key(payload['agent'], AGENT) + '.json')).unlink(missing_ok=True)
            return {'status': 'revoked'}
        if action == 'inference':
            self.generation(payload['agent'], None, payload['payload'], emit, relayed=True)
            return None
        raise ValueError('Invalid provider operation')

    def desktop_bundle(self, identifier, ticket):
        self.pairing(identifier, ticket)
        pair = {'url': self.public_url + '/import/' + identifier, 'ticket': ticket}
        archive = BytesIO()
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
            launcher = zipfile.ZipInfo('Constellation ChatGPT Plan/Connect.command')
            launcher.create_system = 3
            launcher.external_attr = 0o100755 << 16
            bundle.writestr(launcher, (HERE / 'launch-plan.sh').read_bytes())
            for file in ('connect-plan.py', 'connect-plan.py.lock'):
                bundle.writestr('Constellation ChatGPT Plan/' + file, (HERE / file).read_bytes())
            bundle.writestr('Constellation ChatGPT Plan/pair.json', json.dumps(pair))
        return archive.getvalue()


def handler(connections, role):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def setup(self):
            self.request.settimeout(360)
            super().setup()

        def body(self):
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= MAX_BODY:
                raise ValueError('Invalid body')
            return json.loads(self.rfile.read(size))

        def response(self, code, data, content_type='application/json', filename=None):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if filename:
                self.send_header('Content-Disposition', 'attachment; filename="' + filename + '"')
            output = data if isinstance(data, bytes) else json.dumps(data).encode()
            self.send_header('Content-Length', len(output))
            self.end_headers()
            self.wfile.write(output)

        def stream(self, operation):
            self.send_response(200)
            self.send_header('Content-Type', 'application/x-ndjson')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()

            def emit(item):
                self.wfile.write(json.dumps(item, default=lambda value: value.model_dump(mode='json')).encode() + b'\n')
                self.wfile.flush()
            try:
                operation(emit)
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as error:
                emit({'error': 'forbidden' if isinstance(error, (PermissionError, FileNotFoundError)) else provider_error(error)})

        def do_GET(self):
            try:
                if role == 'model' and self.path == '/health':
                    self.response(200, {'ready': True})
                    return
                match = re.fullmatch(r'/(?:hiring/)?desktop/([a-f0-9]{24})/([a-f0-9]{64})', self.path)
                if role != 'enrollment' or not match:
                    raise PermissionError
                bundle = connections.desktop_bundle(*match.groups())
                self.response(200, bundle, 'application/zip', 'Constellation-ChatGPT-Plan.zip')
            except (OSError, ValueError, KeyError):
                self.response(404, {'error': 'This enrollment link is unavailable'})

        def do_POST(self):
            try:
                auth = self.headers.get('Authorization', '')
                bearer = auth.removeprefix('Bearer ') if auth.startswith('Bearer ') else ''
                if role == 'admin' and self.path == '/':
                    payload = self.body()
                    if payload.get('action') == 'inference':
                        self.stream(lambda write: connections.admin(payload, write))
                    else:
                        self.response(200, connections.admin(payload, None))
                    return
                if role == 'model' and self.path.startswith('/agents/'):
                    agent = self.path.removeprefix('/agents/')
                    request = self.body()
                    self.stream(lambda write: connections.generation(agent, bearer, request, write))
                    return
                match = re.fullmatch(r'/(?:hiring/)?import/([a-f0-9]{24})', self.path)
                if role == 'enrollment' and match:
                    self.response(200, connections.import_plan(match[1], bearer, self.body()))
                    return
                raise PermissionError
            except (PermissionError, FileNotFoundError):
                self.response(403, {'error': 'forbidden'})
            except Exception as error:
                self.response(409, {'error': provider_error(error)})
    return Handler


class AdminServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main():
    os.umask(0o077)
    state = Path('/hiring')
    url = os.environ.get('HIRING_PUBLIC_URL', '')
    if url and (urlsplit(url).scheme != 'https' or urlsplit(url).query or urlsplit(url).fragment):
        raise ValueError('Hiring enrollment requires a private HTTPS origin')
    connections = Connections(state, url)
    admin = state / 'admin.sock'
    admin.unlink(missing_ok=True)
    with (AdminServer(str(admin), handler(connections, 'admin')) as control,
          ThreadingHTTPServer(('0.0.0.0', 8081), handler(connections, 'enrollment')) as enrollment,
          ThreadingHTTPServer(('0.0.0.0', 8082), handler(connections, 'model')) as model):
        for server in (control, enrollment):
            Thread(target=server.serve_forever, daemon=True).start()
        model.serve_forever()


if __name__ == '__main__':
    main()
