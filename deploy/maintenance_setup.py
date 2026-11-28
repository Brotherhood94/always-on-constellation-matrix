"""Operator setup for the writable repository drafts and host recovery policy."""

import sys

from agent_picker import checklist, choose_option, terminal_picker
from installer import SOURCE, compose, compose_config, load, open_existing, prepare_images, run, save, service_units
from repository_maintenance import Maintenance, ordinary
from setup_guide import interactive_terminal


def initialize(data, settings, registry):
    if not (settings['systemd'] or settings.get('control_runtime')):
        raise ValueError('The Architect maintenance requires the deployment approval broker.')
    candidates = settings.get('maintenance_restart_services', [])
    maintenance = Maintenance(data)
    maintenance.initialize(SOURCE, settings['project'], candidates, registry)
    config_path = data / 'state/the-architect/config.json'
    config = load(config_path)
    save(config_path, {**config, 'maintenance': True}, mode=0o444)
    compose_config(settings, data, registry)


def choose_services(registry, settings):
    labels = {key: entry['name'] for key, entry in registry['agents'].items() if ordinary(key, registry)}
    initial = settings.get('maintenance_restart_services', [])
    selected = terminal_picker(checklist, '🛠 Choose connectors for automatic recovery', labels, initial, focus_done=True)
    if selected is not None:
        return selected
    return [name for name, label in labels.items()
            if choose_option(f'🛠 Allow recovery for {label} when unhealthy?', {'no': 'No', 'yes': 'Yes'},
                             'yes' if name in initial else 'no') == 'yes']


def configure(data, args):
    maintenance = Maintenance(data)
    if args.action == 'export':
        sys.stdout.write(maintenance.reviewed_diff(args.operation_id))
        return
    if args.action == 'rollback':
        with maintenance.locked():
            outcome = maintenance.recover_release(args.operation_id, retry=True)
        if outcome not in {'rolled_back', 'recovery_required'}:
            raise ValueError('Choose an interrupted maintenance release with a rollback journal.')
        print('Previous release verified.' if outcome == 'rolled_back' else
              'Rollback verification failed. Inspect the affected services before retrying.')
        if outcome != 'rolled_back':
            raise SystemExit(1)
        return
    if args.action in {'pause-recovery', 'resume-recovery'}:
        with maintenance.locked():
            if not (maintenance.root / 'policy.json').exists():
                raise ValueError('Enable maintenance first.')
            pause = maintenance.root / 'paused'
            if args.action == 'pause-recovery':
                save(pause, 'paused\n')
            else:
                pause.unlink(missing_ok=True)
        print('Automatic recovery ' + ('paused.' if args.action == 'pause-recovery' else 'resumed.'))
        return
    if args.no_auto_restarts and args.restart_service:
        raise ValueError('Choose restart services or disable automatic restarts.')
    settings, registry = open_existing(data)
    if not (settings['systemd'] or settings.get('control_runtime')) or not registry['agents'].get('the-architect', {}).get('active'):
        raise ValueError('Configure The Architect before enabling maintenance.')
    services = args.restart_service
    if services is None:
        services = choose_services(registry, settings) if interactive_terminal() and not args.no_auto_restarts else []
    with maintenance.locked():
        settings, registry = open_existing(data)
        if any(not ordinary(name, registry) for name in services):
            raise ValueError('Choose active, local Office connectors for autonomous restart.')
        settings = {**settings, 'maintenance': True, 'maintenance_restart_services': services}
        if getattr(args, 'auto_config_policy', None):
            from auto_tune import policy
            settings['auto_config_policy'] = policy(load(args.auto_config_policy))
        prepare_images(settings, registry)
        initialize(data, settings, registry)
        save(data / 'settings.json', settings)
        service_units(settings, data, broker=True)
        if settings.get('control_runtime'):
            compose(data, 'restart', 'control-runtime')
        else:
            run('systemctl', 'try-restart', settings['project'] + '-root.service')
        compose(data, '--profile', 'agents', 'up', '-d', '--no-deps', '--wait', '--wait-timeout', '150', 'engine-the-architect')
        compose(data, '--profile', 'agents', 'up', '-d', '--no-deps', '--wait', '--wait-timeout', '150', 'the-architect')
    print('The Architect maintenance is ready. Review repository releases in Control Room.')
