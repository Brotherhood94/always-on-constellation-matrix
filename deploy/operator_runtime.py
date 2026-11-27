"""Run the operator CLI in the workspace's Docker administration environment."""
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


SOURCE = Path(__file__).resolve().parents[1]
SOCKET = Path('/var/run/docker.sock')
DOCKER = ('docker', '--host', f'unix://{SOCKET}')
FILE_OPTIONS = {'--config', '--owner-password-file', '--manifest', '--public-key', '--enrollment', '--receipt',
                '--api-file', '--invite', '--token-file', '--ca-file', '--auto-config-policy'}
DIRECTORY_OPTIONS = {'--desktop-mount', '--legacy-data-dir'}


def default_data():
    return (Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'constellation-matrix').absolute()


def image():
    return 'constellation-control:' + sha256((SOURCE / 'deploy/runtime/Dockerfile').read_bytes()).hexdigest()[:16]


def preflight():
    for args, message in ((('info', '--format', '{{json .SecurityOptions}}'), 'Open an SSH session with access to the local Docker Engine.'),
                          (('compose', 'version', '--short'), 'Install the Docker Compose plugin.')):
        try:
            output = subprocess.check_output([*DOCKER, *args], text=True, stderr=subprocess.DEVNULL, timeout=10)
        except (OSError, subprocess.SubprocessError):
            raise ValueError(message) from None
        if args[0] == 'info' and 'name=rootless' in output:
            raise ValueError('Select the standard local Docker Engine for Constellation.')


def mount(path, *, readonly=False, target=None):
    item = str(path)
    if ',' in item or '\n' in item:
        raise ValueError('Use file paths without commas or newlines.')
    return ['--mount', f'type=bind,source={item},target={target or item}' + (',readonly' if readonly else '')]


def selected_data(args):
    for index, value in enumerate(args):
        if value == '--data-dir' and index + 1 < len(args):
            return Path(args[index + 1]).expanduser().absolute()
        if value.startswith('--data-dir='):
            return Path(value.split('=', 1)[1]).expanduser().absolute()
    return default_data()


def inputs(args, data):
    paths = {}
    for index, value in enumerate(args):
        option, separator, attached = value.partition('=')
        if option in FILE_OPTIONS | DIRECTORY_OPTIONS | {'--output'}:
            raw = attached if separator else args[index + 1] if index + 1 < len(args) else ''
            if not raw:
                continue
            path = Path(raw).expanduser().absolute()
            args[index if separator else index + 1] = option + '=' + str(path) if separator else str(path)
            paths[path.parent if option == '--output' else path] = option not in {'--output', '--desktop-mount'}
            if option == '--config' and path.is_file():
                settings = json.loads(path.read_text())
                for key in ('owner_password_file', 'neo4j_password_file', 'telegram_api_file'):
                    if settings.get(key):
                        paths[Path(settings[key]).expanduser().absolute()] = True
        elif value and not value.startswith('-') and Path(value).is_file():
            path = Path(value).resolve()
            args[index] = str(path)
            paths[path] = True
    return {path: ro for path, ro in paths.items() if path.exists() and not path.is_relative_to(data.parent)
            and not path.is_relative_to(SOURCE)}


def telegram_setup_file(args):
    if args[0] == '--data-dir':
        args = args[2:]
    elif args[0].startswith('--data-dir='):
        args = args[1:]
    if args[:1] not in (['install'], ['setup-guide']) and args[:2] != ['telegram', 'setup']:
        return None
    if any(value.split('=', 1)[0] in {'--config', '--api-file', '--print', '--help', '-h'} for value in args):
        return None
    path = Path.home() / 'telegram-api.json'
    return path if path.is_file() and not path.is_symlink() else None


def read_operator_credentials(path):
    target = '/run/telegram-api.json'
    check = '''import os, stat, sys
path = sys.argv[1]
mode = os.stat(path).st_mode
if not stat.S_ISREG(mode) or mode & 0o077:
    sys.exit(1)
with open(path, 'rb') as credential:
    sys.stdout.buffer.write(credential.read())
'''
    try:
        return subprocess.check_output([
            *DOCKER, 'run', '--rm', '--network', 'none', '--read-only', '--log-driver', 'none',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--user', os.environ['CONSTELLATION_OPERATOR_USER'], *mount(path, readonly=True, target=target),
            image(), 'python3', '-B', '-c', check, target,
        ], stderr=subprocess.DEVNULL, timeout=30)
    except (KeyError, OSError, subprocess.SubprocessError) as error:
        raise ValueError('Enter the path to a readable private JSON file on the VPS. Set its permissions to 0600.') from error


def main():
    args = sys.argv[1:]
    if not args or args in (['-h'], ['--help']) or args[0] == 'validate':
        os.execv(sys.executable, [sys.executable, '-B', str(SOURCE / 'deploy/installer.py'), *args])
    try:
        preflight()
        data = selected_data(args)
        if data.is_symlink() or data.resolve() in (Path('/'), SOURCE) or SOURCE.is_relative_to(data.resolve()):
            raise ValueError('Use a dedicated state directory outside the checkout.')
        data.parent.mkdir(mode=0o700, exist_ok=True, parents=True)
        runtime = image()
        if subprocess.run([*DOCKER, 'image', 'inspect', runtime], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode:
            subprocess.run([*DOCKER, 'build', '-t', runtime, str(SOURCE / 'deploy/runtime')], check=True)
        command = [*DOCKER, 'run', '--rm', '-i', '--network', 'host', '-e', 'CONSTELLATION_RUNTIME=compose',
                   '-e', 'PYTHONUNBUFFERED=1', '--workdir', str(Path.cwd())]
        for key in ('TERM', 'NO_COLOR'):
            if key in os.environ:
                command.extend(['-e', key + '=' + os.environ[key]])
        if sys.stdin.isatty() and sys.stdout.isatty():
            command.extend(['-t', '-e', f'CONSTELLATION_OPERATOR_HOME={Path.home()}',
                            '-e', f'CONSTELLATION_OPERATOR_USER={os.getuid()}:{os.getgid()}'])
        mounts = inputs(args, data)
        if credential_file := telegram_setup_file(args):
            mounts[credential_file] = True
            command.extend(['-e', f'CONSTELLATION_TELEGRAM_API_FILE={credential_file}'])
        for path, readonly in [(SOURCE, True), (data.parent, False), (SOCKET, False), *mounts.items()]:
            command.extend(mount(path, readonly=readonly))
        tailscale = shutil.which('tailscale')
        socket = Path('/var/run/tailscale/tailscaled.sock')
        if tailscale and socket.is_socket():
            command.extend(['-e', f'CONSTELLATION_TAILSCALE_BIN={tailscale}', '--mount',
                            f'type=bind,source={tailscale},target=/usr/bin/tailscale,readonly', *mount(socket)])
        command.extend([runtime, 'python3', '-B', str(SOURCE / 'deploy/installer.py'), '--data-dir', str(data), *args])
        os.execvp(command[0], command)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f'Constellation setup failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
