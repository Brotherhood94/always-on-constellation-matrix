"""Install root-owned host services for metrics, encrypted backup and approval."""
from pathlib import Path
import re
import shutil

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
    for action, targets in ((('disable', '--now'), triggers), (('stop',), static_services),
                            (('disable', '--now'), enabled_services)):
        if targets:
            run('systemctl', *action, *(path.name for path in targets))
    installed = triggers + static_services + enabled_services
    if installed:
        for path in installed:
            path.unlink()
        run('systemctl', 'daemon-reload')


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
        root = deployment + '/state/root'
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
