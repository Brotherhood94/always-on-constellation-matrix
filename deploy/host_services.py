"""Install root-owned host services for metrics, encrypted backup and approval."""
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from uuid import uuid4

from installer import SOURCE, directory, run, save

UNITS = Path('/etc/systemd/system')


def safe_path(path):
    value = str(path)
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', value):
        raise ValueError('Host services require a deployment path without spaces or shell characters')
    return value


def unit(name, contents):
    save(UNITS / name, contents, mode=0o644)


def enable_host_dependencies(settings):
    services = ['docker.service', 'containerd.service']
    if settings['tailscale']:
        services.append('tailscaled.service')
    run('systemctl', 'enable', '--now', *services)


def enable_services(settings):
    project = settings['project']
    run('systemctl', 'enable', '--now', project + '-stack.service',
        project + '-monitor.service', project + '-backup.timer')


def remove_units(project):
    descriptions = {
        '-telegram-backup.timer': 'Schedule Constellation Telegram backup',
        '-telegram-metrics.timer': 'Schedule Constellation Telegram metrics',
        '-backup.timer': 'Nightly encrypted Constellation backup',
        '-telegram-backup.service': 'Constellation Telegram backup',
        '-telegram-metrics.service': 'Constellation Telegram metrics',
        '-backup.service': 'Encrypted Constellation checkpoint',
        '-dot.service': 'OpenAI Secure MCP Tunnel for Dot',
        '-root.socket': 'Private socket for approved Root operations',
        '-root.service': 'Execute operator-approved Root operations',
        '-roster.socket': 'Private socket for approved Office roster changes',
        '-roster@.service': 'Apply an approved Office roster change',
        '-roster-reconcile.timer': 'Resume pending Office worker retirements',
        '-roster-reconcile.service': 'Reconcile pending Office worker retirements',
        '-monitor.service': 'Constellation VPS resource collector',
        '-stack.service': 'Private Matrix Constellation deployment',
    }
    triggers, enabled_services, static_services = [], [], []
    for suffix, description in descriptions.items():
        path = UNITS / (project + suffix)
        if path.is_symlink() or not path.is_file():
            continue
        lines = path.read_text().splitlines()
        if f'Description={description}' not in lines:
            continue
        if path.suffix in ('.timer', '.socket'):
            triggers.append(path)
        elif '[Install]' in lines:
            enabled_services.append(path)
        else:
            static_services.append(path)
    for action, targets in ((('disable', '--now'), triggers),
                            (('stop',), [path for path in static_services if not path.name.endswith('@.service')]),
                            (('disable', '--now'), enabled_services)):
        if targets:
            run('systemctl', *action, *(path.name for path in targets))
    if any(path.name == project + '-roster@.service' for path in static_services):
        run('systemctl', 'stop', project + '-roster@*.service')
    installed = triggers + static_services + enabled_services
    if installed:
        for path in installed:
            path.unlink()
        run('systemctl', 'daemon-reload')


def stage_roster_runtime(data):
    # Socket activation grants root. Use the version installed by the operator
    # even if the original checkout is later edited by an unprivileged user.
    host = data / 'host'
    directory(host)
    runtime = host / ('roster-runtime-' + uuid4().hex[:12])
    ignored = shutil.ignore_patterns('.git', 'node_modules', '__pycache__', '.env*', '.pytest_cache')

    def exclude(path, names):
        return ignored(path, names) | {name for name in names if (Path(path) / name).is_symlink()}

    with TemporaryDirectory(dir=host) as workspace:
        source = Path(workspace) / 'runtime'
        directory(source)
        for name in ('deploy', 'agents', 'integrations', 'dashboard'):
            shutil.copytree(SOURCE / name, source / name, ignore=exclude)
        shutil.copyfile(SOURCE / '.dockerignore', source / '.dockerignore')
        source.rename(runtime)
    return runtime


def roster_units(settings, data):
    deployment = safe_path(data)
    source = safe_path(stage_roster_runtime(data))
    name = settings['project'] + '-roster'
    command = f'/usr/bin/python3 -B {source}/deploy/roster_broker.py {deployment}'
    unit(name + '.socket', f'''[Unit]
Description=Private socket for approved Office roster changes
[Socket]
ListenStream=/run/{name}.sock
Accept=yes
SocketMode=0660
SocketGroup=991
RemoveOnStop=yes
[Install]
WantedBy=sockets.target
''')
    unit(name + '@.service', f'''[Unit]
Description=Apply an approved Office roster change
After=docker.service
Requires=docker.service
[Service]
ExecStart={command}
StandardInput=socket
StandardOutput=journal
StandardError=journal
UMask=0077
NoNewPrivileges=yes
''')
    unit(name + '-reconcile.service', f'''[Unit]
Description=Reconcile pending Office worker retirements
After=docker.service {settings['project']}-stack.service
Requires=docker.service
[Service]
Type=oneshot
ExecStart={command} reconcile
UMask=0077
NoNewPrivileges=yes
''')
    unit(name + '-reconcile.timer', f'''[Unit]
Description=Resume pending Office worker retirements
[Timer]
OnBootSec=90s
OnUnitActiveSec=30s
[Install]
WantedBy=timers.target
''')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', name + '.socket', name + '-reconcile.timer')


def enable_telegram_units(settings, data):
    if not settings['systemd']:
        raise ValueError('Enable host systemd services to schedule Telegram jobs')
    deployment = safe_path(data)
    source = safe_path(SOURCE)
    timers = []
    for task, schedule in (('backup', 'OnCalendar=*-*-* 04:15:00'),
                           ('metrics', 'OnBootSec=90s\nOnUnitActiveSec=60s')):
        name = settings['project'] + '-telegram-' + task
        backup_ready = (f'ConditionPathExists={deployment}/backups/config\n'
                        f'ConditionPathExists={deployment}/telegram/bridge/config.yaml\n') if task == 'backup' else ''
        unit(name + '.service', f'''[Unit]
Description=Constellation Telegram {task}
After=docker.service {settings['project']}-stack.service
Requires=docker.service
{backup_ready}[Service]
Type=oneshot
Environment=TELEGRAM_STACK_DIR={deployment}
ExecStart=/usr/bin/bash {source}/integrations/telegram/telegram {task}
''')
        unit(name + '.timer', f'''[Unit]
Description=Schedule Constellation Telegram {task}
[Timer]
{schedule}
Persistent=true
[Install]
WantedBy=timers.target
''')
        timers.append(name + '.timer')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', *timers)


def units(settings, data, *, broker=False):
    project = settings['project']
    deployment = safe_path(data)
    host = data / 'host'
    directory(host)
    for filename in ('monitor.py', 'agent_catalog.py'):
        shutil.copyfile(SOURCE / 'deploy/matrix' / filename, host / filename)
    shutil.copyfile(SOURCE / 'deploy/recovery.py', host / 'recovery.py')
    if settings['telegram']:
        shutil.copyfile(SOURCE / 'integrations/telegram/compose.yaml', host / 'telegram-compose.yaml')
    slice_file = UNITS / 'matrix-workload.slice'
    if not slice_file.exists():
        shutil.copyfile(SOURCE / 'deploy/matrix/systemd/matrix-workload.slice', slice_file)
        slice_file.chmod(0o644)
    unit(project + '-monitor.service', f'''[Unit]
Description=Constellation VPS resource collector
After=docker.service {project}-stack.service
Requires=docker.service
[Service]
Type=simple
WorkingDirectory={deployment}
EnvironmentFile={deployment}/.env
ExecStart=/usr/bin/python3 -B {deployment}/host/monitor.py
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
[Install]
WantedBy=multi-user.target
''')
    unit(project + '-stack.service', f'''[Unit]
Description=Private Matrix Constellation deployment
After=docker.service
Requires=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory={deployment}
ExecStart=/usr/bin/docker compose --profile agents start
ExecStop=/usr/bin/docker compose --profile agents stop
[Install]
WantedBy=multi-user.target
''')
    unit(project + '-backup.service', f'''[Unit]
Description=Encrypted Constellation checkpoint
After=docker.service {project}-stack.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -B {deployment}/host/recovery.py {deployment} backup
''')
    unit(project + '-backup.timer', f'''[Unit]
Description=Nightly encrypted Constellation backup
[Timer]
OnCalendar=*-*-* 03:30
Persistent=true
[Install]
WantedBy=timers.target
''')
    if broker:
        shutil.copyfile(SOURCE / 'integrations/root/broker.py', host / 'root-broker.py')
        name = project + '-root'
        root = deployment + '/state/vps-manager'
        unit(name + '.socket', f'''[Unit]
Description=Private socket for approved Root operations
[Socket]
ListenStream=/run/{name}.sock
SocketMode=0660
SocketGroup=992
RemoveOnStop=yes
[Install]
WantedBy=sockets.target
''')
        unit(name + '.service', f'''[Unit]
Description=Execute operator-approved Root operations
Requires={name}.socket
[Service]
Type=simple
ExecStart=/usr/bin/python3 -B {deployment}/host/root-broker.py {root}/config.json {root}/execution {root}/operations.json
UMask=0077
''')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'start', 'matrix-workload.slice')
    if broker:
        run('systemctl', 'enable', '--now', project + '-root.socket')
    if settings.get('roster_management'):
        roster_units(settings, data)
