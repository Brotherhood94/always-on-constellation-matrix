"""Supervise the typed deployment services in the Docker control container."""
from datetime import datetime
from pathlib import Path
import os
import signal
import socket
import subprocess
import sys
from threading import Thread
import time
from zoneinfo import ZoneInfo

from installer import SOURCE, load


def listener(path, group):
    path.unlink(missing_ok=True)
    peer = socket.socket(socket.AF_UNIX)
    peer.bind(str(path))
    os.chown(path, 0, group)
    path.chmod(0o660)
    peer.listen(16)
    return peer


def roster(data):
    import hiring
    from roster import reconcile_retirements
    from roster_broker import serve
    with listener(data / 'runtime/roster.sock', 991) as server:
        server.settimeout(5)
        next_reconcile = 0
        while True:
            try:
                peer, _ = server.accept()
            except TimeoutError:
                pass
            else:
                with peer:
                    try:
                        serve(data, peer)
                    except (TimeoutError, OSError):
                        pass
            if time.monotonic() >= next_reconcile and (data / 'roster/inviter-token').is_file():
                next_reconcile = time.monotonic() + 15
                try:
                    reconcile_retirements(data)
                    hiring.reconcile(data)
                except Exception as error:
                    print('Roster reconciliation will retry', type(error).__name__, flush=True)


def scheduled(data):
    from installer import operator_lock, save
    from recovery import backup
    from operation_timing import refresh
    next_check = 0
    while True:
        time.sleep(5)
        if time.monotonic() < next_check:
            continue
        next_check = time.monotonic() + 60
        try:
            settings = load(data / 'settings.json')
            now = datetime.now(ZoneInfo(settings.get('helper_hints_timezone', 'UTC')))
            try:
                from auto_tune import reconcile
                reconcile(data)
            except Exception as error:
                print('Automatic recovery check will retry', type(error).__name__, flush=True)
            try:
                refresh(data, now)
            except Exception as error:
                print('Operation timings will retry', type(error).__name__, flush=True)
            record = data / 'runtime/last-backup'
            if now.hour != 3 or now.minute < 30 or (record.is_file() and record.read_text() == str(now.date())):
                continue
            with operator_lock(data):
                backup(data, 'backup')
                save(record, str(now.date()))
        except Exception as error:
            print('Scheduled backup will retry', type(error).__name__, flush=True)


def main(data):
    children = {}
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    for target in (roster, scheduled):
        Thread(target=target, args=(data,), daemon=True).start()
    root = data / 'state/the-architect'
    try:
        while True:
            scripts = {'monitor': (SOURCE / 'deploy/matrix/monitor.py',)}
            if (data / 'mobility/endpoint.json').is_file():
                scripts['mobility'] = (SOURCE / 'deploy/mobility.py', data, 'serve')
            if (root / 'config.json').is_file():
                scripts['executor'] = (SOURCE / 'integrations/root/broker.py', root / 'config.json',
                                       root / 'execution', root / 'operations.json')
            for item in (data / 'twins').glob('*/job.json'):
                job = load(item)
                name = 'twin-' + job['id']
                if name in children and children[name].poll() is None:
                    continue
                if job['status'] in {'stopping', 'failed', 'starting'} or (job['status'] == 'ready' and time.time() > job['expires_at']):
                    from twin import stop
                    try:
                        stop(data, job['id'])
                    except Exception as error:
                        print('Twin cleanup will retry', type(error).__name__, flush=True)
                elif job['status'] == 'queued':
                    children[name] = subprocess.Popen([sys.executable, '-B', '-m', 'twin', str(data), job['id']])
            for name, command in scripts.items():
                if name not in children or children[name].poll() is not None:
                    children[name] = subprocess.Popen([sys.executable, '-B', *map(str, command)])
            time.sleep(5)
    finally:
        for process in children.values():
            if process.poll() is None:
                process.terminate()


if __name__ == '__main__':
    main(Path(sys.argv[1]))
