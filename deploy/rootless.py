"""Run provisioning in the operator's local rootless Docker user namespace."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

IMAGE = 'constellation-matrix-operator:28.2.2-1'
NAMESPACE = 'CONSTELLATION_ROOTLESS_NAMESPACE'


def docker_socket(uid):
    # Match Docker's context precedence and then pin the endpoint for every call.
    host = os.environ.get('DOCKER_HOST')
    if os.environ.get('DOCKER_CONTEXT') or not host:
        host = subprocess.check_output(
            ['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], text=True).strip()
    if not host.startswith('unix:///'):
        raise ValueError('Select a local rootless Docker Unix socket')
    path = Path(host.removeprefix('unix://')).resolve()
    try:
        endpoint = path.stat()
    except FileNotFoundError:
        raise ValueError('The Docker socket is missing. Start your rootless Docker daemon') from None
    if not stat.S_ISSOCK(endpoint.st_mode) or endpoint.st_uid != uid:
        raise ValueError('Select a rootless Docker context whose socket belongs to your account')
    cli = ('docker', '--host', f'unix://{path}')
    env = {key: value for key, value in os.environ.items() if key != 'DOCKER_CONTEXT'}
    security = json.loads(subprocess.check_output(
        [*cli, 'info', '--format', '{{json .SecurityOptions}}'], text=True, env=env))
    if not any(option.split(',')[0] == 'name=rootless' for option in security or []):
        raise ValueError('Docker must be running in rootless mode for this command')
    return path, cli, env


def mount(source, target, *, readonly=False):
    # Docker parses --mount as CSV. Reject path delimiters before invoking it.
    if any(character in str(path) for character in ',"\n\r' for path in (source, target)):
        raise ValueError('Rootless Docker paths cannot contain commas, quotes, or line breaks')
    return ['--mount', f'type=bind,source={source},target={target}' + (',readonly' if readonly else '')]


def launch(source, data, arguments, inputs, *, tailnet=''):
    uid = os.geteuid()
    try:
        socket, cli, env = docker_socket(uid)
    except FileNotFoundError:
        raise ValueError('Install Docker Engine and Compose V2, then select your rootless Docker context') from None
    if data == Path.home() or data.is_relative_to(source) or source.is_relative_to(data):
        raise ValueError('Use a dedicated deployment directory outside the checkout')
    if data.exists() and (not data.is_dir() or data.stat().st_uid != uid):
        raise ValueError('Rootless deployment storage must belong to your account')

    input_mounts = []
    for path in sorted({Path(os.path.abspath(path)) for path in inputs if path}):
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != uid:
            raise ValueError('Rootless input files must be regular files owned by your account')
        if not (path.is_relative_to(data) or path.is_relative_to(source)):
            input_mounts += mount(path, path, readonly=True)

    command = [*cli, 'run', '--rm', '-i', '--network', 'host',
               '--security-opt', 'no-new-privileges:true', '--workdir', os.getcwd(),
               '--env', f'{NAMESPACE}=1', '--env', f'CONSTELLATION_TAILNET={tailnet}',
               '--env', 'DOCKER_HOST=unix:///var/run/docker.sock']
    if sys.stdin.isatty() and sys.stdout.isatty():
        command.append('-t')
    command += mount(socket, '/var/run/docker.sock')
    command += mount(source, source, readonly=True)
    command += mount(data, data)
    command += input_mounts
    command += [IMAGE, 'python3', '-B', str(source / 'deploy/installer.py'), '--data-dir', str(data), *arguments]
    data.mkdir(parents=True, mode=0o700, exist_ok=True)
    available = subprocess.run([*cli, 'image', 'inspect', IMAGE], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, env=env).returncode == 0
    if not available:
        subprocess.run([*cli, 'build', '-t', IMAGE, str(source / 'deploy/rootless')], check=True, env=env)
    result = subprocess.run(command, env=env)
    if result.returncode:
        raise SystemExit(result.returncode)
