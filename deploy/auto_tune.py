"""Apply only preapproved Compose startup and memory changes."""
from copy import deepcopy
import json
import re
import time

from installer import compose, load, operator_lock, run, save, up


def policy(candidate):
    if not isinstance(candidate, dict) or set(candidate) != {'services', 'memory_budget_mib'}:
        raise ValueError('Specify services and memory_budget_mib.')
    entries = candidate['services']
    if not isinstance(entries, dict) or len(entries) > 32 or type(candidate['memory_budget_mib']) is not int or not 0 <= candidate['memory_budget_mib'] <= 65536:
        raise ValueError('Invalid recovery policy limits.')
    for service, limits in entries.items():
        if not re.fullmatch(r'(?:engine-)?[a-z0-9][a-z0-9_-]{0,36}', service) or not isinstance(limits, dict) or not limits:
            raise ValueError('Use an eligible local agent service for automatic tuning.')
        for field, value in limits.items():
            if field not in {'start_period_seconds', 'memory_mib'} or type(value) is not int or not (1 <= value <= (180 if field == 'start_period_seconds' else 8192)):
                raise ValueError('Only bounded startup and memory limits can be preapproved.')
    return candidate


def propose(service, health, limits, budget):
    memory = health.get('HostConfig', {}).get('Memory', 0) // (1024 * 1024)
    state = health.get('State', {})
    if state.get('OOMKilled') is True and memory and limits.get('memory_mib', 0) > memory and budget > 0:
        return 'mem_limit', min(limits['memory_mib'], memory + max(32, memory // 4), memory + budget)
    checks = state.get('Health', {})
    current = service.get('healthcheck', {}).get('start_period', '0s')
    if not isinstance(current, str) or not re.fullmatch(r'[0-9]+s', current):
        return None
    seconds = int(current[:-1])
    entries = checks.get('Log', [])
    from datetime import datetime
    try:
        start = datetime.fromisoformat(state['StartedAt'].replace('Z', '+00:00'))
        ready = datetime.fromisoformat(entries[-1]['End'].replace('Z', '+00:00'))
    except (KeyError, IndexError, ValueError, AttributeError):
        return None
    if (checks.get('Status') == 'healthy' and len(entries) >= 3 and entries[-1]['ExitCode'] == 0 and
            all(entry.get('ExitCode') for entry in entries[:-1]) and 0 < (ready - start).total_seconds() <= 180 and
            limits.get('start_period_seconds', 0) > seconds):
        return 'start_period', min(limits['start_period_seconds'], max(seconds + 5, int((ready - start).total_seconds()) + 5))
    return None


def reconcile(data):
    from pathlib import Path
    from subprocess import SubprocessError
    from deployment_snapshot import publish
    from repository_maintenance import ordinary
    settings = load(data / 'settings.json')
    configured = settings.get('auto_config_policy')
    if not configured or (data / 'maintenance/paused').exists():
        return
    settings_path = data / 'runtime/tuned.json'
    manifest_path = data / 'compose.yaml'
    journal = data / 'runtime/tuning-journal.json'
    with operator_lock(data):
        registry = load(data / 'agents.json')
        approved = policy(configured)
        tuned = load(settings_path) if settings_path.is_file() else {}
        current = load(manifest_path)
        if journal.is_file():
            prior = load(journal)
            save(manifest_path, prior['manifest'])
            save(settings_path, prior['tuned'])
            up(data, prior['service'])
            journal.unlink()
            return
        project = settings['project']
        spent = sum(entry.get('added_mib', 0) for entry in tuned.values())
        free = next((int(line.split()[1]) // 1024 for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:')), 0)
        available = min(free, max(0, approved['memory_budget_mib'] - spent))
        for key, limits in approved['services'].items():
            agent = key.removeprefix('engine-')
            if key not in current['services'] or not ordinary(agent, registry):
                continue
            if (data / 'state' / agent / 'matrix/relocation-drain').exists():
                continue
            container = compose(data, '--profile', 'agents', 'ps', '-a', '-q', key, capture_output=True, text=True).stdout.split()
            if len(container) != 1:
                continue
            inspection = json.loads(run('docker', 'inspect', container[0], capture_output=True, text=True).stdout)[0]
            if inspection['Config']['Labels'].get('com.docker.compose.project') != project:
                continue
            incident = inspection.get('Id', '') + ':' + inspection.get('State', {}).get('StartedAt', '')
            previous = tuned.get(key, {})
            if previous.get('incident') == incident or time.time() - previous.get('at', 0) < 900:
                continue
            candidate = propose(current['services'][key], inspection, limits, available)
            if not candidate:
                continue
            field, value = candidate
            altered = deepcopy(current)
            target = altered['services'][key]
            was = previous.get('added_mib', 0)
            next_state = {**previous, 'incident': incident, 'at': int(time.time())}
            if field == 'mem_limit':
                target[field] = f'{value}m'
                next_state.update(mem_limit=target[field], added_mib=was + value - inspection['HostConfig']['Memory'] // (1024 * 1024))
            else:
                target['healthcheck'][field] = f'{value}s'
                next_state[field] = target['healthcheck'][field]
            save(journal, {'manifest': current, 'service': key, 'tuned': tuned})
            save(manifest_path, altered)
            try:
                compose(data, 'config', '--quiet')
                up(data, key)
            except (OSError, SubprocessError):
                save(manifest_path, current)
                up(data, key)
                tuned[key] = {**previous, 'incident': incident, 'at': next_state['at']}
                label = 'rolled back'
            else:
                tuned[key] = next_state
                publish(data, settings, registry, altered)
                label = 'verified'
            save(settings_path, tuned)
            journal.unlink()
            from hashlib import sha256
            import os
            receipt = sha256(f'{key}:{incident}:{field}'.encode()).hexdigest()[:32]
            path = data / 'monitor/outbox' / (receipt + '.json')
            save(path, {'id': receipt, 'time': time.time(), 'body': f'Automatic recovery · {key} · {field} {value} · {label}.'}, mode=0o640)
            os.chown(path, 0, 991)
            break


def overrides(data, manifest, settings):
    path = data / 'runtime/tuned.json'
    if path.is_file() and settings.get('auto_config_policy'):
        for key, values in load(path).items():
            if key not in manifest['services'] or key not in settings['auto_config_policy']['services']:
                continue
            target = manifest['services'][key]
            allowed = settings['auto_config_policy']['services'][key]
            if 'mem_limit' in values and int(values['mem_limit'][:-1]) <= allowed.get('memory_mib', 0):
                target['mem_limit'] = values['mem_limit']
            if 'start_period' in values and int(values['start_period'][:-1]) <= allowed.get('start_period_seconds', 0):
                target['healthcheck']['start_period'] = values['start_period']
