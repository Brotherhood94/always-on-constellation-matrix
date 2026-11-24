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
import struct
import subprocess
import sys
import time
from urllib.parse import quote


MAX_MESSAGE = 24_000
WINDOW = 900_000
REVIEWED_SCRIPT = 'reviewed-script'
EVENT_ID = re.compile(r'\$[^\s\x00-\x1f]{1,254}')


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def validate(payload, approvers, operations, now):
    if (not isinstance(payload, dict) or payload.get('action') not in {'approve', 'reject'} or
            payload.get('approver') not in approvers or not isinstance(payload.get('expires'), int) or
            not isinstance(payload.get('decided'), int)):
        raise ValueError('Invalid authority.')
    operation = payload.get('operation')
    dynamic = operation == REVIEWED_SCRIPT
    keys = ('request', 'decision', 'proposal', 'poll') if dynamic else ('request', 'decision')
    for key in keys:
        if not isinstance(payload.get(key), str) or not EVENT_ID.fullmatch(payload[key]):
            raise ValueError('Invalid Matrix receipt.')
    if not re.fullmatch(r'[a-f0-9]{32}', payload.get('id', '')):
        raise ValueError('Invalid operation.')
    if dynamic:
        script = payload.get('script')
        if not isinstance(script, str) or not script.strip() or not re.fullmatch(r'[\t\n\x20-\x7e]{1,4000}', script):
            raise ValueError('Invalid reviewed script.')
        if len({payload[key] for key in keys}) != len(keys):
            raise ValueError('Matrix receipts must be distinct.')
        reviewed = script
        choice = json.dumps([payload['id'], sha(script), payload['proposal'], payload['action']],
                            ensure_ascii=False, separators=(',', ':'))
        if payload.get('choice') != sha(choice):
            raise ValueError('The decision does not bind the reviewed script.')
    else:
        if not isinstance(operation, str) or operation not in operations:
            raise ValueError('Operation is not registered.')
        reviewed = ' '.join(operations[operation]['argv'])
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
    source, review = config['agents'], config['audit']
    if (payload.get('source_room') != source or payload.get('review_room') != review or
            payload['id'] != sha(source + ':' + payload['request'])[:32]):
        raise ValueError('Unexpected source or review room.')
    request = fetch_event(source, payload['request'])
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
    with db:
        db.execute("UPDATE audit SET outcome='unknown' WHERE outcome='running'")
    return db


def handle(db, payload, approvers, operations, now=None, runner=execute, script_runner=execute_script,
           verify_review=None):
    realtime = now is None
    now = int(time.time() * 1000) if now is None else now
    validate(payload, approvers, operations, now)
    digest, identifier, receipt = payload['hash'], payload['id'], payload['decision']
    existing = db.execute(
        'SELECT digest, decision, requested, approver, action, outcome, exit_code FROM audit WHERE id=?',
        (identifier,),
    ).fetchone()
    if existing:
        if existing[:5] != (digest, receipt, payload['request'], payload['approver'], payload['action']):
            raise ValueError('An existing receipt belongs to this operation.')
        return {'outcome': existing[5], 'exit_code': existing[6]}
    if db.execute('SELECT 1 FROM audit WHERE decision=?', (receipt,)).fetchone():
        raise ValueError('This decision has already been used.')
    outcome = 'denied'
    if payload['action'] == 'approve':
        outcome = 'running' if now < payload['expires'] and payload['decided'] >= now - WINDOW else 'expired'
    dynamic = payload['operation'] == REVIEWED_SCRIPT
    if dynamic:
        if verify_review is None:
            raise ValueError('Reviewed scripts are disabled.')
        if outcome in {'running', 'denied'}:
            verify_review(payload)
        if outcome == 'running' and realtime and int(time.time() * 1000) >= payload['expires']:
            outcome = 'expired'
    with db:
        db.execute('INSERT INTO audit VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)', (
            identifier, digest, receipt, payload['request'], payload['approver'],
            payload['action'], payload['decided'], outcome))
    code = None
    if outcome == 'running':
        try:
            if dynamic:
                outcome, code = script_runner(payload['script'])
            else:
                outcome, code = runner(operations[payload['operation']]['argv'])
        except OSError:
            outcome = 'launch_failed'
        with db:
            db.execute('UPDATE audit SET outcome=?, exit_code=? WHERE id=?', (outcome, code, identifier))
    return {'outcome': outcome, 'exit_code': code}


def serve(config, state, operations_path):
    if os.geteuid() != 0 or os.environ.get('LISTEN_PID') != str(os.getpid()) or os.environ.get('LISTEN_FDS') != '1':
        raise SystemExit('Run this broker through its root systemd socket.')
    os.umask(0o077)
    allow = frozenset((config['owner'],))
    db = database(state / 'execution.sqlite3')
    verifier = None
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

        def verifier(payload):
            verify_matrix_receipt(config, payload, lambda room, event: matrix_event(port, token, room, event))

    listener = socket.socket(fileno=3)
    while True:
        peer, _ = listener.accept()
        with peer:
            peer.settimeout(5)
            _, uid, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
            if uid != config['gateway_uid']:
                continue
            try:
                with peer.makefile('rb') as stream:
                    body = stream.readline(MAX_MESSAGE + 1)
                if len(body) > MAX_MESSAGE or not body.endswith(b'\n'):
                    raise ValueError('Invalid request size.')
                result = handle(db, json.loads(body), allow, read_operations(operations_path), verify_review=verifier)
            except (ValueError, TypeError, KeyError, AttributeError, OSError, sqlite3.Error):
                result = {'error': 'Execution request rejected.'}
            try:
                peer.sendall(json.dumps(result).encode() + b'\n')
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
        if operation == REVIEWED_SCRIPT or not re.fullmatch('[a-z][a-z0-9-]{0,80}', operation):
            raise ValueError('Invalid operation name.')
        argv = definition['argv']
        if (not isinstance(argv, list) or not argv or not argv[0].startswith('/') or
                not all(isinstance(arg, str) and re.fullmatch(r'[a-zA-Z0-9_./@=:+-]+', arg) for arg in argv)):
            raise ValueError('Registered commands must use unambiguous fixed arguments.')
    return operations


if __name__ == '__main__':
    serve(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]), Path(sys.argv[3]))
