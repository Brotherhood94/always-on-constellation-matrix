"""Connect the local Dot bridge through OpenAI's outbound Secure MCP Tunnel."""
from getpass import getpass
from hashlib import sha256
import io
from pathlib import Path
import platform
import re
from urllib.request import urlopen
from zipfile import ZipFile

from installer import directory, run, save
from host_services import safe_path, unit

RELEASE = 'v0.0.21'
HASHES = {
    'amd64': 'fa53182b766aeaceb652d86aee291c48daf7b90b5d73586dba370f4c1801325e',
    'arm64': '164b757ee0c0389508e6e17e129b0080f7733d8b3108e0bd1d7fe31e01016f97',
}


def install_client(destination):
    build = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(platform.machine())
    if build not in HASHES:
        raise ValueError('The pinned tunnel supports Linux amd64 and arm64')
    asset = f'tunnel-client-runtime-{RELEASE}-linux-{build}.zip'
    with urlopen(f'https://github.com/openai/tunnel-client/releases/download/{RELEASE}/{asset}', timeout=60) as reply:
        archive = reply.read(64 * 1024 * 1024)
    if sha256(archive).hexdigest() != HASHES[build]:
        raise ValueError('OpenAI tunnel release checksum differs from the expected digest')
    with ZipFile(io.BytesIO(archive)) as bundle:
        entries = [name for name in bundle.namelist() if Path(name).name == 'tunnel-client-runtime']
        if len(entries) != 1:
            raise ValueError('OpenAI tunnel release has an unexpected executable layout')
        destination.write_bytes(bundle.read(entries[0]))
        destination.chmod(0o755)


def connect(settings, data, tunnel_id):
    if not settings['dot'] or not settings['systemd']:
        raise ValueError('Enable Dot and systemd for this deployment')
    dot = data / 'state/dot/matrix'
    if not (dot / 'bridge.sock').is_socket():
        raise ValueError('Start Dot and wait for its Unix socket first')
    if not re.fullmatch(r'tunnel_(?:[a-z0-9]{4}_)?[a-z0-9]{32}', tunnel_id):
        raise ValueError('Use a valid OpenAI tunnel ID')
    configuration = data / 'dot'
    directory(configuration)
    executable = configuration / 'tunnel-client'
    if not executable.is_file():
        install_client(executable)
    credential = configuration / 'runtime-key'
    if not credential.exists():
        value = getpass('OpenAI tunnel runtime API key: ')
        if not re.fullmatch(r'sk-[a-zA-Z0-9_-]+', value):
            raise ValueError('Invalid runtime key format')
        save(credential, value)
    name = settings['project'] + '-dot.service'
    root = safe_path(data)
    unit(name, f'''[Unit]
Description=OpenAI Secure MCP Tunnel for Dot
After=network-online.target docker.service {settings['project']}-stack.service
Wants=network-online.target
[Service]
Type=simple
LoadCredential=runtime-key:{root}/dot/runtime-key
LoadCredential=bridge-token:{root}/state/dot/matrix/bridge-token
ExecStart={root}/dot/tunnel-client run --control-plane.tunnel-id {tunnel_id} --control-plane.api-key file:%d/runtime-key --mcp.server-url url=http://localhost/mcp,unix-socket={root}/state/dot/matrix/bridge.sock --mcp.extra-headers "X-Dot-Bridge-Token: file:%d/bridge-token" --mcp.oauth-discovery disabled --health.listen-addr 127.0.0.1:18786
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
PrivateTmp=yes
[Install]
WantedBy=multi-user.target
''')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', name)
    run('systemctl', 'restart', name)
    run('curl', '--fail', '--silent', '--show-error', '--retry', '10', '--retry-all-errors',
        '--max-time', '4', 'http://127.0.0.1:18786/readyz', '-o', '/dev/null')
    print('Dot tunnel is ready.')
