"""Create an encrypted, consistent checkpoint and verify its isolated restore."""
import fcntl
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from tempfile import TemporaryDirectory


def run(*args, **options):
    return subprocess.run(args, check=True, **options)


def services(data):
    result = run('docker', 'compose', '--project-directory', str(data), '--profile', 'agents',
                 'ps', '--services', '--filter', 'status=running', capture_output=True, text=True)
    return result.stdout.splitlines()


def backup(data, mode):
    repository = data / 'backups'
    key = data / 'secrets/restic-password'
    restic = ('restic', '--repo', str(repository), '--password-file', str(key))
    if not key.exists():
        if (repository / 'config').exists():
            raise ValueError('Recover the existing backup password first')
        with key.open('x') as stream:
            stream.write(secrets.token_urlsafe(48) + '\n')
    if not (repository / 'config').exists():
        run(*restic, 'init')
    if mode == 'restore-check':
        with TemporaryDirectory(prefix='constellation-restore-') as work:
            run(*restic, 'restore', 'latest', '--tag', 'constellation', '--verify', '--target', work)
            restored = Path(work) / data.relative_to('/')
            settings = json.loads((restored / 'settings.json').read_text())
            rooms = json.loads((restored / 'rooms.json').read_text())
            if settings['project'] == '' or not rooms.get('office') or not (restored / 'postgres/PG_VERSION').is_file():
                raise ValueError('Restored checkpoint is missing deployment state')
        print('Encrypted checkpoint restored and verified in an isolated directory.')
        return

    compose = ('docker', 'compose', '--project-directory', str(data), '--profile', 'agents')
    running = services(data)
    telegram = tuple()
    if any((data / name).exists() for name in ('telegram/connector/config.json', 'telegram/guide/token')):
        source = Path(__file__).resolve().parents[1] / 'integrations/telegram/compose.yaml'
        settings = json.loads((data / 'settings.json').read_text())
        installed = data / 'host/telegram-compose.yaml'
        if installed.exists():
            source = installed
        telegram_command = ('docker', 'compose', '--env-file', str(data / '.env'), '-p', settings['project'] + '-telegram',
                            '-f', str(source), '--profile', 'agent')
        telegram = tuple(run(*telegram_command, 'ps', '--services', '--filter', 'status=running',
                             capture_output=True, text=True).stdout.splitlines())
    try:
        if telegram:
            run(*telegram_command, 'stop', *telegram)
        if running:
            run(*compose, 'stop', *running)
        attempt = run(*restic, 'backup', '--tag', 'constellation-pending', '--json',
                      '--exclude', str(repository), '--exclude', str(key),
                      '--exclude', str(data / 'staging'), str(data), stdout=subprocess.PIPE, text=True)
        snapshot = next(json.loads(line)['snapshot_id'] for line in reversed(attempt.stdout.splitlines())
                        if json.loads(line).get('message_type') == 'summary')
        run(*restic, 'tag', '--add', 'constellation', '--remove', 'constellation-pending', snapshot)
    finally:
        try:
            if running:
                run(*compose, 'start', *running)
        finally:
            if telegram:
                run(*telegram_command, 'start', *telegram)
    run(*restic, 'forget', '--tag', 'constellation', '--keep-last', '7', '--prune')
    from datetime import datetime, timezone
    (data / 'last-successful-backup').write_text(datetime.now(timezone.utc).isoformat() + '\n')


if __name__ == '__main__':
    os.umask(0o077)
    data = Path(sys.argv[1])
    action = sys.argv[2]
    if action not in {'backup', 'restore-check'}:
        raise SystemExit('Use backup or restore-check')
    with (data / '.operator.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        backup(data, action)
