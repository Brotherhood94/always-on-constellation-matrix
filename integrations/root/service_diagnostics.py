"""Read local deployment service state and summarize bounded log evidence."""
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess


SERVICE_ID = re.compile(r'[a-z0-9][a-z0-9._@-]{0,79}')
UNIT_ID = re.compile(r'[a-z0-9][a-z0-9._@-]{0,119}\.(?:service|socket|timer)')
DOCKER_STATES = {'created', 'restarting', 'running', 'removing', 'paused', 'exited', 'dead'}
UNIT_STATES = {'active', 'inactive', 'failed', 'activating', 'deactivating', 'reloading', 'maintenance', 'refreshing'}
SIGNALS = {
    'authentication': re.compile(r'AuthenticationError|AUTH_KEY_UNREGISTERED|SESSION_REVOKED|M_UNKNOWN_TOKEN|authentication failed|unauthorized', re.I),
    'configuration': re.compile(r'invalid configuration|missing configuration|active, verified binding|binding (?:was paused|missing|invalid|inactive)', re.I),
    'lookup': re.compile(r'USERNAME_NOT_OCCUPIED|PEER_ID_INVALID|no eligible chat matched|could not resolve|name resolution failed', re.I),
    'connectivity': re.compile(r'connection refused|timed out|ETIMEDOUT|ECONNREFUSED|sync or delivery check failed', re.I),
    'resource pressure': re.compile(r'out of memory|OOMKilled|no space left|disk full', re.I),
    'reported errors': re.compile(r'\b(?:error|fatal|critical)\b|\b(?:\w+Error|Exception):', re.I),
}
LOG_SERVICE_LIMIT = 6
FINDINGS_LIMIT = 2400
LEGACY_TELEGRAM = {'telegram-bridge', 'telegram-guide', 'connector', 'telegram-engine'}


@dataclass(frozen=True)
class Service:
    source: str
    name: str
    status: str
    healthy: bool
    log_id: str | None = None


def command(*argv, merge_stderr=False):
    try:
        result = subprocess.run(argv, capture_output=True, encoding='utf-8', errors='replace', timeout=8,
                                env={'HOME': '/root', 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    return result.stdout + ('\n' + result.stderr if merge_stderr else '')


def containers(project, source, known):
    listing = command('/usr/bin/docker', 'ps', '--all', '--filter', 'label=com.docker.compose.project=' + project,
                      '--filter', 'label=com.docker.compose.oneoff=False', '--format',
                      '{{.ID}}\t{{.Label "com.docker.compose.service"}}\t{{.State}}\t{{.Status}}')
    if listing is None:
        return None
    services = []
    for line in listing.splitlines():
        fields = line.split('\t', 3)
        if len(fields) != 4:
            continue
        identifier, name, state, status = fields
        if not SERVICE_ID.fullmatch(name) or not re.fullmatch(r'[a-f0-9]{12,64}', identifier):
            continue
        state = state if state in DOCKER_STATES else 'unknown'
        health = re.search(r'\((?:health: )?(healthy|unhealthy|starting)\)\s*$', status)
        services.append(Service(source, name, state + (f' ({health[1]})' if health else ''),
                                state == 'running' and (not health or health[1] == 'healthy'), identifier))
    missing = known - {service.name for service in services}
    services.extend(Service(source, name, 'no container found', False) for name in sorted(missing))
    return services


def host_units(project, tailscale):
    dependencies = ('docker.service', 'containerd.service', *(('tailscaled.service',) if tailscale else ()))
    listing = command('/usr/bin/systemctl', 'list-units', '--all', '--plain', '--full', '--no-pager', '--no-legend',
                      '--type=service,socket,timer', project + '-*', *dependencies)
    if listing is None:
        return None
    services = {}
    for line in listing.splitlines():
        fields = line.split(maxsplit=4)
        if len(fields) < 4:
            continue
        unit, loaded, active, substate = fields[:4]
        if not UNIT_ID.fullmatch(unit) or not (unit.startswith(project + '-') or unit in dependencies):
            continue
        if active not in UNIT_STATES:
            active = 'unknown'
        substate = substate if re.fullmatch(r'[a-z-]{1,32}', substate) else 'unknown'
        detail = substate if loaded == 'loaded' else 'unit unavailable'
        healthy = loaded == 'loaded' and (active == 'active' or (active == 'inactive' and substate == 'dead'))
        services[unit] = Service('systemd', unit, f'{active} ({detail})', healthy, unit if loaded == 'loaded' else None)
    for path in Path('/etc/systemd/system').glob(project + '-*'):
        unit = path.name
        if UNIT_ID.fullmatch(unit) and '@.' not in unit and unit not in services:
            services[unit] = Service('systemd', unit, 'installed, not loaded', True, unit)
    return services.values()


def matches(service, target, project):
    if target == 'all':
        return True
    if service.source == 'systemd':
        name = service.name.removeprefix(project + '-')
        return (target in {service.name, name, name.rsplit('.', 1)[0]} or
                (target == 'telegram' and name.startswith('telegram-')) or
                (target == 'dashboard' and name in {'monitor.service', 'tailscaled.service'}))
    if target == 'telegram':
        return service.source == 'Telegram Compose' or service.name == target
    if target == 'context-intelligence':
        return service.source == 'Compose' and service.name in {'context', 'neo4j', target}
    return service.name == target


def signal_summary(service):
    if service.source == 'systemd':
        logs = command('/usr/bin/journalctl', '--unit', service.log_id, '--since=-30min', '--lines=100',
                       '--no-pager', '--output=cat', '--quiet')
    else:
        logs = command('/usr/bin/docker', 'logs', '--since', '30m', '--tail', '100', service.log_id, merge_stderr=True)
    if logs is None:
        return 'recent logs unavailable'
    lines = logs.splitlines()
    counts = [f'{label} ({count})' for label, pattern in SIGNALS.items()
              if (count := sum(pattern.search(line) is not None for line in lines))]
    return ', '.join(counts) if counts else 'no recognized signals in recent logs'


def inspect_services(data, target, read_config, *, legacy=False):
    settings = read_config(data / 'settings.json')
    project = settings.get('project')
    if not isinstance(project, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}', project):
        return 'precondition_failed', None, None
    groups = []
    if not legacy:
        manifest = data / 'compose.yaml'
        configured = read_config(manifest)['services'] if manifest.is_file() else {}
        known = {name for name in configured if SERVICE_ID.fullmatch(name)}
        groups.append(('Compose', containers(project, 'Compose', known)))
    if settings.get('telegram'):
        telegram = containers(project + '-telegram', 'Telegram Compose', LEGACY_TELEGRAM if legacy else set())
        if telegram is not None and (data / 'telegram/engine-moved').exists():
            telegram = [service for service in telegram if service.name != 'telegram-engine']
            telegram.append(Service('Telegram Compose', 'telegram-engine', 'managed on a worker', True))
        if telegram is not None and legacy:
            telegram = [service for service in telegram if service.name in LEGACY_TELEGRAM]
        groups.append(('Telegram Compose', telegram))
    elif legacy:
        return 'completed', 0, {'findings': 'Telegram is disabled in this deployment.'}
    if not legacy and settings.get('systemd'):
        groups.append(('systemd', host_units(project, settings.get('tailscale'))))
    findings = [f'{source}: status unavailable' for source, services in groups if services is None]
    selected = sorted((service for _, services in groups if services is not None for service in services
                       if matches(service, target, project)), key=lambda service: (service.healthy, service.source, service.name))
    if not selected:
        if target == 'telegram' and not settings.get('telegram'):
            findings.append('Telegram is disabled in this deployment.')
        else:
            findings.append('No matching local service found. Use the service overview to check available names.')
    log_checks = 0
    omitted_logs = False
    for service in selected:
        line = f'{service.name} [{service.source}]: {service.status}'
        if target != 'all' and service.log_id:
            if log_checks < LOG_SERVICE_LIMIT:
                line += ' · ' + signal_summary(service)
                log_checks += 1
            else:
                omitted_logs = True
        if sum(len(item) + 1 for item in findings) + len(line) > FINDINGS_LIMIT - 150:
            findings.append('More services are available. Inspect a specific service for details.')
            break
        findings.append(line)
    if omitted_logs:
        findings.append('Only the first six matching instances had logs checked. Inspect a specific service for details.')
    return 'completed', 0, {'findings': '\n'.join(findings)}
