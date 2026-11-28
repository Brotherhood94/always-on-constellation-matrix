#!/usr/bin/env python3
import fcntl
import hashlib
from http.client import HTTPConnection, HTTPException
import json
import os
from pathlib import Path
import re
import signal
import socket
import sqlite3
import stat
import struct
import subprocess
import sys
from tempfile import NamedTemporaryFile
import time
from urllib.parse import quote

from service_diagnostics import SERVICE_ID, inspect_services


MAX_MESSAGE = 40_000
WINDOW = 900_000
REVIEWED_SCRIPT = 'reviewed-script'
REVIEWED_PLAN = 'reviewed-plan'
MAINTENANCE_RELEASE = 'maintenance-release'
EVENT_ID = re.compile(r'\$[^\s\x00-\x1f]{1,254}')


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def validate(payload, approvers, operations, now):
    if (not isinstance(payload, dict) or payload.get('action') not in {'approve', 'reject'} or
            payload.get('approver') not in approvers or not isinstance(payload.get('expires'), int) or
            not isinstance(payload.get('decided'), int)):
        raise ValueError('Invalid authority.')
    operation = payload.get('operation')
    dynamic = operation in (REVIEWED_SCRIPT, REVIEWED_PLAN, MAINTENANCE_RELEASE)
    review = dynamic or 'proposal' in payload
    keys = ('request', 'decision', 'proposal', 'poll') if review else ('request', 'decision')
    for key in keys:
        if not isinstance(payload.get(key), str) or not EVENT_ID.fullmatch(payload[key]):
            raise ValueError('Invalid Matrix receipt.')
    if not re.fullmatch(r'[a-f0-9]{32}', payload.get('id', '')):
        raise ValueError('Invalid operation.')
    if operation == REVIEWED_PLAN:
        reviewed = payload.get('plan')
        read_plan(reviewed)
    elif operation == MAINTENANCE_RELEASE:
        reviewed = payload.get('plan')
        if not isinstance(reviewed, str) or len(reviewed.encode()) > 4000 or not isinstance(json.loads(reviewed), dict):
            raise ValueError('Invalid tested release.')
    elif dynamic:
        script = payload.get('script')
        if not isinstance(script, str) or not script.strip() or not re.fullmatch(r'[\t\n\x20-\x7e]{1,4000}', script):
            raise ValueError('Invalid reviewed script.')
        reviewed = script
    else:
        if not isinstance(operation, str) or operation not in operations:
            raise ValueError('Operation is not registered.')
        reviewed = ' '.join(operations[operation]['argv'])
    if review:
        if len({payload[key] for key in keys}) != len(keys):
            raise ValueError('Matrix receipts must be distinct.')
        choice = json.dumps([payload['id'], sha(reviewed), payload['proposal'], payload['action']],
                            ensure_ascii=False, separators=(',', ':'))
        if payload.get('choice') != sha(choice):
            raise ValueError('The decision does not bind the reviewed operation.')
    if sha(reviewed) != payload.get('hash'):
        raise ValueError('Changed command.')
    if payload['decided'] > now + 60_000 or not payload['decided'] <= payload['expires'] <= payload['decided'] + WINDOW:
        raise ValueError('Invalid approval window.')


def execute(argv, timeout=120, pass_fds=()):
    child = subprocess.Popen(
        argv, cwd='/root',
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={'HOME': '/root', 'USER': 'root', 'LOGNAME': 'root', 'LANG': 'C.UTF-8',
             'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'},
        start_new_session=True,
        pass_fds=pass_fds,
    )
    try:
        code = child.wait(timeout=timeout)
        return ('completed' if code == 0 else 'failed'), code
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()
        return 'timed_out', None


def execute_script(script):
    fd = os.memfd_create('root-reviewed-script', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    with os.fdopen(fd, 'wb') as contents:
        contents.write(script.encode('ascii'))
        contents.flush()
        contents.seek(0)
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW |
                    fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        return execute(['/bin/bash', '--noprofile', '--norc', f'/proc/self/fd/{fd}'], pass_fds=(fd,))


def read_plan(raw):
    if not isinstance(raw, str) or len(raw.encode()) > 15_000:
        raise ValueError('Invalid reviewed plan.')
    plan = json.loads(raw)
    fields = {'shell': {'script', 'verify'}, 'inspect-file': {'path'},
              'replace-file': {'path', 'before', 'after', 'verify'}, 'inspect-services': {'target'}, 'restart-service': {'target'},
              'twin-create': {'account', 'model', 'effort', 'agents', 'minutes', 'revision', 'overrides'},
              'telegram-diagnostics': set()}
    kind = plan.get('kind') if isinstance(plan, dict) else None
    if not isinstance(kind, str) or kind not in fields or set(plan) != {'kind', *fields[kind]}:
        raise ValueError('Invalid reviewed plan.')
    if kind == 'twin-create':
        from twin import validate
        validate(plan)
    if kind in {'inspect-services', 'restart-service'} and (not isinstance(plan['target'], str) or not SERVICE_ID.fullmatch(plan['target'])):
        raise ValueError('Invalid service inspection target.')
    for name in ('script', 'verify'):
        if name in plan and (not isinstance(plan[name], str) or not plan[name].strip() or
                             not re.fullmatch(r'[\t\n\x20-\x7e]{1,4000}', plan[name])):
            raise ValueError('Invalid reviewed command.')
    if 'path' in plan and (not isinstance(plan['path'], str) or len(plan['path']) > 512 or
                           not re.fullmatch(r'/[A-Za-z0-9_./@+=-]+', plan['path'])):
        raise ValueError('Specify one absolute file path.')
    if kind == 'replace-file' and (not all(isinstance(plan[key], str) and len(plan[key].encode()) <= 4096
                                          and '\x00' not in plan[key] for key in ('before', 'after')) or
                                   plan['before'] == plan['after']):
        raise ValueError('The review must contain the exact old and new text.')
    return plan


def read_regular(path):
    if path.resolve(strict=True) != path:
        raise ValueError('Use the canonical path of a regular file.')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4096:
            raise ValueError('Only bounded regular files can be read.')
        content = handle.read(4097)
        if len(content) > 4096:
            raise ValueError('The file changed while it was read.')
    return metadata, content


def redacted_preview(path, content):
    secret = re.compile(r'password|passwd|shadow|credential|token|secret|private.key|api.key|authorization|\.env|\.ssh',
                        re.IGNORECASE)
    if path.parts[1:2] in {('proc',), ('sys',), ('dev',)} or secret.search(str(path)):
        return '[Private file content withheld]'
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError:
        return '[Non-text content withheld]'
    if any(ord(char) < 32 and char not in '\n\t\r' for char in text):
        return '[Non-text content withheld]'
    if re.search(r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----', text):
        return '[Private file content withheld]'
    opaque = re.compile(r'[A-Za-z0-9+/=_-]{24,}|://[^\s/:]+:[^\s/@]+@')
    lines = ['[Redacted]' if secret.search(line) or opaque.search(line) else line for line in text.splitlines()]
    return '\n'.join(lines)[:1400]


def replace_regular(path, expected, content, metadata):
    with NamedTemporaryFile(dir=path.parent, prefix='.the-architect-', delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(content)
            os.fchown(handle.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(handle.fileno(), stat.S_IMODE(metadata.st_mode))
            handle.flush()
            os.fsync(handle.fileno())
            current, existing = read_regular(path)
            if (existing != expected or (current.st_mode, current.st_uid, current.st_gid, current.st_ino) !=
                    (metadata.st_mode, metadata.st_uid, metadata.st_gid, metadata.st_ino)):
                raise ValueError('File changed before the replacement.')
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def read_diagnostic_config(path):
    require_root_file(path)
    return json.loads(path.read_text())


def execute_plan(raw, state, identifier, *, run_script=execute_script):
    plan = read_plan(raw)
    kind = plan['kind']
    if kind == 'twin-create':
        from twin import enqueue
        try:
            enqueue(state.parents[2], identifier, plan)
        except ValueError as error:
            return 'precondition_failed', None, {'findings': str(error)}
        return 'completed', 0, {'findings': 'Digital twin queued. Use twin status ' + identifier + '.'}
    if kind == 'restart-service':
        from service_diagnostics import restart_service
        return restart_service(state.parents[2], plan['target'], read_diagnostic_config)
    if kind in {'inspect-services', 'telegram-diagnostics'}:
        return inspect_services(state.parents[2], plan.get('target', 'telegram'), read_diagnostic_config,
                                legacy=kind == 'telegram-diagnostics')
    if kind == 'shell':
        outcome, code = run_script(plan['script'])
        if outcome != 'completed':
            return outcome, code, None
        outcome, code = run_script(plan['verify'])
        return ('completed' if outcome == 'completed' else 'verification_failed'), code, None
    path = Path(plan['path'])
    try:
        metadata, original = read_regular(path)
        if kind == 'inspect-file':
            return 'completed', 0, {'sha256': hashlib.sha256(original).hexdigest(),
                                     'preview': redacted_preview(path, original)}
        updated = plan['after'].encode()
        if original != plan['before'].encode():
            return 'file_changed', None, None
        backup = state / (identifier + '.previous')
        with backup.open('xb') as file:
            file.write(original)
            file.flush()
            os.fsync(file.fileno())
        replace_regular(path, original, updated, metadata)
    except (ValueError, OSError):
        return 'precondition_failed', None, None
    try:
        verified, code = run_script(plan['verify'])
    except OSError:
        verified, code = 'launch_failed', None
    try:
        current, live = read_regular(path)
        if live != updated or (current.st_mode, current.st_uid, current.st_gid) != (
                metadata.st_mode, metadata.st_uid, metadata.st_gid):
            return 'recovery_required', code, None
        if verified == 'completed':
            return 'completed', code, None
        replace_regular(path, updated, original, current)
    except (ValueError, OSError):
        return 'recovery_required', code, None
    return 'rolled_back', code, None


def matrix_event(port, token, room, event):
    path = f"/_matrix/client/v3/rooms/{quote(room, safe='')}/event/{quote(event, safe='')}"
    connection = HTTPConnection('127.0.0.1', port, timeout=4)
    try:
        connection.request('GET', path, headers={'Authorization': 'Bearer ' + token})
        response = connection.getresponse()
        data = response.read(96_001)
        if response.status != 200 or len(data) > 96_000:
            raise ValueError('Matrix could not confirm the receipt.')
        raw = json.loads(data)
    except HTTPException as error:
        raise ValueError('Matrix could not confirm the receipt.') from error
    finally:
        connection.close()
    if (raw.get('event_id') != event or raw.get('room_id', room) != room or raw.get('type') != 'm.room.encrypted' or
            type(raw.get('origin_server_ts')) is not int or raw.get('unsigned', {}).get('redacted_because')):
        raise ValueError('Matrix returned a different, unencrypted or redacted receipt.')
    return raw


def verify_matrix_receipt(config, payload, fetch_event):
    source, review = payload.get('source_room'), config['audit']
    if (source not in {config['agents'], review} or payload.get('review_room') != review or
            payload['id'] != sha(source + ':' + payload['request'])[:32]):
        raise ValueError('Unexpected source or review room.')
    request = fetch_event(source, payload['request'])
    if source == review and request.get('sender') not in {config['owner'], config.get('dot')}:
        raise ValueError('Control Room requests require the operator or the configured Dot identity.')
    proposal = fetch_event(review, payload['proposal'])
    poll = fetch_event(review, payload['poll'])
    vote = fetch_event(review, payload['decision'])
    times = [item['origin_server_ts'] for item in (request, proposal, poll, vote)]
    if (proposal.get('sender') != config['root'] or poll.get('sender') != config['root'] or
            vote.get('sender') != config['owner'] or times != sorted(times) or
            times[-1] != payload['decided'] or payload['expires'] > times[1] + WINDOW):
        raise ValueError('Matrix did not confirm the approving account and chronology.')
    # Synapse supplies the event and its exposed relation. The trusted gateway
    # supplies the choice after authenticating and decrypting the vote.
    relation = vote.get('content', {}).get('m.relates_to', {})
    if relation.get('rel_type') != 'm.reference' or relation.get('event_id') != payload['poll']:
        raise ValueError('The Matrix vote points to a different poll.')


def database(path):
    db = sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    db.execute('''CREATE TABLE IF NOT EXISTS audit (
      id TEXT PRIMARY KEY, digest TEXT NOT NULL, decision TEXT UNIQUE NOT NULL, requested TEXT NOT NULL,
      approver TEXT NOT NULL, action TEXT NOT NULL, decided INTEGER NOT NULL,
      outcome TEXT NOT NULL, exit_code INTEGER
    )''')
    if 'result' not in {row[1] for row in db.execute('PRAGMA table_info(audit)')}:
        db.execute('ALTER TABLE audit ADD COLUMN result TEXT')
    with db:
        db.execute("UPDATE audit SET outcome='unknown' WHERE outcome='running'")
    return db


def handle(db, payload, approvers, operations, now=None, runner=execute, script_runner=execute_script,
           verify_review=None, plan_runner=None, require_review=False, maintenance=None):
    realtime = now is None
    now = int(time.time() * 1000) if now is None else now
    validate(payload, approvers, operations, now)
    operation = payload['operation']
    if require_review and ('proposal' not in payload or operation == REVIEWED_SCRIPT):
        raise ValueError('The Architect requires a reviewed operation with verification.')
    digest, identifier, receipt = payload['hash'], payload['id'], payload['decision']
    existing = db.execute(
        'SELECT digest, decision, requested, approver, action, outcome, exit_code, result FROM audit WHERE id=?',
        (identifier,),
    ).fetchone()
    if existing:
        if existing[:5] != (digest, receipt, payload['request'], payload['approver'], payload['action']):
            raise ValueError('An existing receipt belongs to this operation.')
        if operation == MAINTENANCE_RELEASE and maintenance and existing[5] in ('unknown', 'running'):
            with maintenance.locked():
                status = maintenance.recover_release(identifier)
                if status:
                    with db:
                        db.execute('UPDATE audit SET outcome=? WHERE id=?', (status, identifier))
                    return {'outcome': status}
        return {'outcome': existing[5], 'exit_code': existing[6],
                **({'result': json.loads(existing[7])} if existing[7] else {})}
    if db.execute('SELECT 1 FROM audit WHERE decision=?', (receipt,)).fetchone():
        raise ValueError('This decision has already been used.')
    outcome = 'denied'
    if payload['action'] == 'approve':
        outcome = 'running' if now < payload['expires'] and payload['decided'] >= now - WINDOW else 'expired'
    if operation == REVIEWED_SCRIPT or 'proposal' in payload:
        if verify_review is None:
            raise ValueError('Reviewed scripts are disabled.')
        if outcome in {'running', 'denied'}:
            verify_review(payload)
        if outcome == 'running' and realtime and int(time.time() * 1000) >= payload['expires']:
            outcome = 'expired'
    with db:
        db.execute('INSERT INTO audit (id,digest,decision,requested,approver,action,decided,outcome) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (
            identifier, digest, receipt, payload['request'], payload['approver'],
            payload['action'], payload['decided'], outcome))
    code = result = None
    if outcome == 'running':
        try:
            if operation == REVIEWED_PLAN:
                if plan_runner is None:
                    raise ValueError('Reviewed maintenance plans are disabled.')
                outcome, code, result = plan_runner(payload['plan'], identifier)
            elif operation == MAINTENANCE_RELEASE:
                if maintenance is None:
                    raise ValueError('The Architect maintenance is disabled.')
                try:
                    with maintenance.locked():
                        release = json.loads(payload['plan'])
                        outcome = maintenance.activate(identifier, release)
                        if outcome == 'completed':
                            result = {'revision': release['revision']}
                except BlockingIOError:
                    outcome = 'precondition_failed'
            elif operation == REVIEWED_SCRIPT:
                outcome, code = script_runner(payload['script'])
            else:
                outcome, code = runner(operations[payload['operation']]['argv'])
        except (OSError, subprocess.SubprocessError):
            outcome = 'launch_failed'
        except ValueError:
            if operation != MAINTENANCE_RELEASE:
                raise
            outcome = 'precondition_failed'
        with db:
            db.execute('UPDATE audit SET outcome=?, exit_code=?, result=? WHERE id=?',
                       (outcome, code, json.dumps(result) if result else None, identifier))
    return {'outcome': outcome, 'exit_code': code, **({'result': result} if result else {})}


def maintenance_request(maintenance, config, payload, fetch_event):
    action = payload.get('maintenance')
    if maintenance is None or action not in {'prepare', 'seal', 'scan'}:
        raise ValueError('The Architect maintenance is disabled.')
    with maintenance.locked():
        if action == 'scan':
            return {'receipts': maintenance.scan()[-10:]}
        event = payload.get('request')
        identifier = payload.get('id')
        source = payload.get('source_room', config['agents'])
        if (source not in {config['agents'], config['audit']} or not isinstance(event, str)
                or not EVENT_ID.fullmatch(event) or identifier != sha(source + ':' + event)[:32]):
            raise ValueError('A maintenance workspace requires a verified Office or Control Room request.')
        raw = fetch_event(source, event)
        if source == config['audit'] and raw['sender'] not in {config['owner'], config.get('dot')}:
            raise ValueError('Use the operator or Dot in Control Room to request maintenance.')
        if raw['sender'] == config['root'] or raw['origin_server_ts'] + 2 * WINDOW < int(time.time() * 1000):
            raise ValueError('The maintenance request is stale or is from the executor.')
        return maintenance.prepare(identifier) if action == 'prepare' else maintenance.seal(identifier)


def serve(config, state, operations_path):
    container = os.environ.get('CONSTELLATION_RUNTIME') == 'compose'
    if not container and (os.geteuid() != 0 or os.environ.get('LISTEN_PID') != str(os.getpid()) or os.environ.get('LISTEN_FDS') != '1'):
        raise SystemExit('Run this broker through its root systemd socket.')
    os.umask(0o077)
    allow = frozenset((config['owner'],))
    db = database(state / 'execution.sqlite3')
    verifier = None
    fetch_event = None
    maintenance = None
    if config.get('maintenance'):
        from maintenance import Maintenance
        maintenance = Maintenance(state.parents[2])
    reviewed = operations_path.with_name('approved-scripts.json')
    if reviewed.exists():
        require_root_file(reviewed)
        port = json.loads(reviewed.read_text())['matrix_port']
        if type(port) is not int or not 1 <= port <= 65535:
            raise SystemExit('Invalid Synapse loopback port for reviewed scripts.')
        token_file = operations_path.parent / 'gateway-credentials/matrix-token'
        require_root_file(token_file)
        token = token_file.read_text().strip()
        if not token or not token.isascii() or not token.isprintable():
            raise SystemExit('Invalid Root Matrix authentication file.')

        def fetch_event(room, event):
            return matrix_event(port, token, room, event)

        def verifier(payload):
            verify_matrix_receipt(config, payload, fetch_event)

    if container:
        from control_runtime import listener as open_listener
        listener = open_listener(state.parents[2] / 'runtime/executor.sock', config['gateway_uid'])
    else:
        listener = socket.socket(fileno=3)
    while True:
        peer, _ = listener.accept()
        with peer:
            peer.settimeout(5)
            _, uid, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
            if uid != config['gateway_uid']:
                continue
            payload = None
            try:
                with peer.makefile('rb') as stream:
                    body = stream.readline(MAX_MESSAGE + 1)
                if len(body) > MAX_MESSAGE or not body.endswith(b'\n'):
                    raise ValueError('Invalid request size.')
                payload = json.loads(body)
                if container and isinstance(payload, dict) and payload.get('twin') in {'choices', 'models'}:
                    from twin import choices, models
                    result = choices(state.parents[2]) if payload['twin'] == 'choices' else models(state.parents[2], payload['account'])
                elif isinstance(payload, dict) and 'maintenance' in payload:
                    result = maintenance_request(maintenance, config, payload, fetch_event)
                else:
                    if container and (payload['operation'] not in {REVIEWED_PLAN, MAINTENANCE_RELEASE} or
                                      (payload['operation'] == REVIEWED_PLAN and read_plan(payload['plan'])['kind']
                                       not in {'inspect-services', 'restart-service', 'twin-create'})):
                        raise ValueError('The deployment broker accepts only scoped workspace operations.')
                    result = handle(db, payload, allow, read_operations(operations_path), verify_review=verifier,
                                    require_review=True, plan_runner=lambda raw, key: execute_plan(raw, state, key),
                                    maintenance=maintenance)
            except ValueError as error:
                details = str(error) if isinstance(payload, dict) and payload.get('maintenance') == 'seal' else ''
                result = {'error': details if details and len(details) < 200 else 'Execution request rejected.'}
            except (TypeError, KeyError, AttributeError, OSError, sqlite3.Error, subprocess.SubprocessError):
                result = {'error': 'Execution request rejected.'}
            try:
                peer.sendall(json.dumps(result, ensure_ascii=False).encode() + b'\n')
            except OSError:
                pass


def require_root_file(path):
    metadata = path.lstat()
    if metadata.st_uid != 0 or metadata.st_mode & 0o022 or path.is_symlink():
        raise ValueError('Approval configuration must be controlled by the system owner.')


def read_operations(path):
    for entry in (path.parent, path):
        require_root_file(entry)
    operations = json.loads(path.read_text())
    for operation, definition in operations.items():
        if operation in (REVIEWED_SCRIPT, REVIEWED_PLAN, MAINTENANCE_RELEASE) or not re.fullmatch('[a-z][a-z0-9-]{0,80}', operation):
            raise ValueError('Invalid operation name.')
        argv = definition['argv']
        if (not isinstance(argv, list) or not argv or not argv[0].startswith('/') or
                not all(isinstance(arg, str) and re.fullmatch(r'[a-zA-Z0-9_./@=:+-]+', arg) for arg in argv)):
            raise ValueError('Registered commands must use unambiguous fixed arguments.')
    return operations


if __name__ == '__main__':
    config_path = Path(sys.argv[1])
    require_root_file(config_path)
    config = json.loads(config_path.read_text())
    if not config['root'].startswith('@the-architect:'):
        raise SystemExit('Migrate the legacy Root agent before enabling the host broker.')
    serve(config, Path(sys.argv[2]), Path(sys.argv[3]))
