#!/usr/bin/env python3
import hashlib
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


MAX_MESSAGE = 8192
WINDOW = 900_000


def validate(payload, approvers, operations, now):
    if (not isinstance(payload, dict) or payload.get('action') not in {'approve', 'reject'} or
            payload.get('approver') not in approvers or not isinstance(payload.get('expires'), int) or
            not isinstance(payload.get('decided'), int)):
        raise ValueError('Invalid authority.')
    for key in ('request', 'decision'):
        if not re.fullmatch(r'\$[^\s\x00-\x1f]{1,254}', payload.get(key, '')):
            raise ValueError('Invalid Matrix receipt.')
    if not re.fullmatch(r'[a-f0-9]{32}', payload.get('id', '')):
        raise ValueError('Invalid operation.')
    operation = payload.get('operation')
    if not isinstance(operation, str) or operation not in operations:
        raise ValueError('Operation is not registered.')
    argv = operations[operation]['argv']
    if hashlib.sha256(' '.join(argv).encode()).hexdigest() != payload.get('hash'):
        raise ValueError('Changed command.')
    if payload['decided'] > now + 60_000 or not payload['decided'] <= payload['expires'] <= payload['decided'] + WINDOW:
        raise ValueError('Invalid approval window.')


def execute(argv, timeout=120):
    child = subprocess.Popen(
        argv, cwd='/root',
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={'HOME': '/root', 'USER': 'root', 'LOGNAME': 'root', 'LANG': 'C.UTF-8',
             'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'},
        start_new_session=True,
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


def handle(db, payload, approvers, operations, now=None, runner=execute):
    now = int(time.time() * 1000) if now is None else now
    validate(payload, approvers, operations, now)
    digest, identifier, receipt = payload['hash'], payload['id'], payload['decision']
    existing = db.execute('SELECT digest, decision, outcome, exit_code FROM audit WHERE id=?', (identifier,)).fetchone()
    if existing:
        if existing[:2] != (digest, receipt):
            raise ValueError('An existing receipt belongs to this operation.')
        return {'outcome': existing[2], 'exit_code': existing[3]}
    if db.execute('SELECT 1 FROM audit WHERE decision=?', (receipt,)).fetchone():
        raise ValueError('This decision has already been used.')
    outcome = 'denied'
    if payload['action'] == 'approve':
        outcome = 'running' if now < payload['expires'] and payload['decided'] >= now - WINDOW else 'expired'
    with db:
        db.execute('INSERT INTO audit VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)', (
            identifier, digest, receipt, payload['request'], payload['approver'],
            payload['action'], payload['decided'], outcome))
    code = None
    if outcome == 'running':
        try:
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
                result = handle(db, json.loads(body), allow, read_operations(operations_path))
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
                result = {'error': 'Execution request rejected.'}
            try:
                peer.sendall(json.dumps(result).encode() + b'\n')
            except OSError:
                pass


def read_operations(path):
    for directory in (path.parent, path):
        metadata = directory.lstat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022 or directory.is_symlink():
            raise ValueError('Operation registry must be controlled by the system owner.')
    operations = json.loads(path.read_text())
    for operation, definition in operations.items():
        if not re.fullmatch('[a-z][a-z0-9-]{0,80}', operation):
            raise ValueError('Invalid operation name.')
        argv = definition['argv']
        if (not isinstance(argv, list) or not argv or not argv[0].startswith('/') or
                not all(isinstance(arg, str) and re.fullmatch(r'[a-zA-Z0-9_./@=:+-]+', arg) for arg in argv)):
            raise ValueError('Registered commands must use unambiguous fixed arguments.')
    return operations


if __name__ == '__main__':
    serve(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]), Path(sys.argv[3]))
