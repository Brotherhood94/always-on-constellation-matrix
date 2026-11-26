"""Run only the coordinator’s typed relocation jobs on an enrolled Linux worker."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import re
import signal
import subprocess
from subprocess import CalledProcessError, SubprocessError
from tarfile import TarError
from tempfile import TemporaryDirectory
import time

from installer import KEY, RUNTIME_UID, directory, load, run, save
from mobility_runtime import Runtime, TELEGRAM, checkpoint, desktop_ready
from mobility_transport import Channel, ID, OPERATIONS


def safe_key(key):
    if not isinstance(key, str) or not KEY.fullmatch(key) or len(key) > 36:
        raise ValueError('Invalid agent on worker')
    return key


def installation(data, key):
    return data / 'worker-agents' / safe_key(key)


def worker_runtime(data, details, worker):
    return Runtime(installation(data, details['key']), details, local=False,
                   desktop=Path(worker['desktop']), host=worker['host'])


@contextmanager
def assignment_lock(runtime, *, wait=True):
    import fcntl
    directory(runtime.root, mode=0o711)
    with (runtime.root / 'worker.lock').open('a') as lock:
        mode = fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(lock, mode)
        yield


def details_for_job(data, job):
    details = job['details']
    key = safe_key(details['key'])
    if (not ID.fullmatch(job['id']) or not ID.fullmatch(details['generation']) or
            not ID.fullmatch(details['transaction']) or job['operation'] not in OPERATIONS or
            ('transfer' in job and not ID.fullmatch(job['transfer']))):
        raise ValueError('Invalid relocation job')
    saved = installation(data, key) / 'runtime.json'
    if job['operation'] not in ('prepare', 'restore') and saved.exists():
        previous = load(saved)
        # The job identity is new. The running runtime and TCP listener are from its installed generation.
        if job['operation'] in ('checkpoint', 'recover'):
            return {**previous, 'transaction': details['transaction'],
                    **({'port': previous['port']} if 'port' in previous else {})}
    return details


def perform(data, worker, channel, job):
    try:
        details = details_for_job(data, job)
        runtime = worker_runtime(data, details, worker)
        operation = job['operation']
        with assignment_lock(runtime):
            if operation == 'prepare':
                runtime.preflight()
            elif operation in ('checkpoint', 'recover'):
                if operation == 'recover' and not runtime.restored(job['details']['generation']):
                    runtime.fence(source=False, drain=False)
                    return {'ok': True, 'checkpoint': False}
                if details.get('legacy_device') and operation == 'checkpoint' and not runtime.compose.is_file():
                    adoption = load(runtime.root / 'legacy.json')
                    if adoption['device'] != details['legacy_device']:
                        return {'ok': False, 'code': 'legacy_device_mismatch'}
                    origin = Path(adoption['state'])
                elif not runtime.compose.is_file():
                    return {'ok': False, 'code': 'source_not_enrolled'}
                else:
                    runtime.fence()
                    stop_serve(details)
                    origin = runtime.state
                with TemporaryDirectory(dir=runtime.root, prefix='checkpoint-') as work:
                    path = Path(work) / 'agent.tar.gz'
                    checkpoint(origin, runtime.key, path)
                    channel.upload(job['transfer'], path)
            elif operation == 'restore':
                if runtime.compose.is_file():
                    running = runtime.running(runtime.command, [*runtime.connectors, runtime.engine])
                    if running and (running & set(runtime.connectors) or not runtime.restored(details['generation'])):
                        raise ValueError('The destination still has a running copy')
                with TemporaryDirectory(dir=runtime.root, prefix='incoming-') as work:
                    path = Path(work) / 'agent.tar.gz'
                    channel.download(job['transfer'], path)
                    runtime.install(path)
                return {'ok': True, **runtime.probe()}
            elif operation == 'verify':
                return {'ok': True, **runtime.probe()}
        return {'ok': True}
    except CalledProcessError as error:
        code = 'provider_sign_in' if error.returncode == 78 else 'host_process_failed'
    except (OSError, ValueError, KeyError, SubprocessError, TarError) as error:
        code = 'host_unavailable' if isinstance(error, OSError) else 'host_precondition_failed'
    return {'ok': False, 'code': code}


def stop_serve(details):
    if details.get('port'):
        subprocess.run(['tailscale', 'serve', '--bg', f"--tcp={details['port']}", 'off'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=15)


def update_role_snapshots(data, response):
    dashboards = {name: snapshot for name, snapshot in response.get('snapshots', {}).items()
                  if name in {'agents.json', 'integrations.json', 'metrics.json'} and isinstance(snapshot, dict)}
    for key in response['leases']:
        root = installation(data, key)
        directory(root, mode=0o711)
        old = root / 'runtime.json'
        if not old.is_file():
            continue
        destination = root / 'dashboard'
        directory(destination, mode=0o755)
        for name, snapshot in dashboards.items():
            save(destination / name, snapshot, mode=0o644)
        workshop = response.get('workshop', {}).get(key)
        if key in {'hiring-manager', 'coach', 'scouter'} and isinstance(workshop, list):
            save(root / 'state' / key / 'matrix/workshop.json', workshop, uid=RUNTIME_UID)


def desired_runtime(runtime, generation):
    fence = runtime.root / 'fenced'
    return (runtime.restored(generation) and
            (not fence.is_file() or fence.read_text().strip() != generation))


def reconcile(data, worker, leases, staging, previous, busy):
    health = {key: state for key, state in previous.items() if key == busy and key in leases}
    for file in (data / 'worker-agents').glob('*/runtime.json'):
        details = load(file)
        key = details['key']
        if key == busy:
            continue
        runtime = worker_runtime(data, details, worker)
        generation = leases.get(key)
        try:
            with assignment_lock(runtime, wait=False):
                if generation == details['generation'] and desired_runtime(runtime, generation):
                    if not runtime.ready():
                        if runtime.engine not in runtime.running(runtime.command, [runtime.engine]):
                            runtime.warm()
                        runtime.start()
                    health[key] = {'generation': generation, 'ready': runtime.ready()}
                elif staging.get(key) == details['generation'] and desired_runtime(runtime, details['generation']):
                    if runtime.engine not in runtime.running(runtime.command, [runtime.engine]):
                        runtime.warm()
                elif runtime.running(runtime.command, [*runtime.connectors, runtime.engine]):
                    # A temporary network outage fences execution without making the installation
                    # permanently unstartable. An explicit checkpoint writes a durable fence.
                    runtime.command('stop', '--timeout', '20', *runtime.connectors, runtime.engine, timeout=90)
        except (OSError, ValueError, SubprocessError):
            continue
    return health


def supervise(data):
    worker = load(data / 'worker.json')
    channel = Channel(worker)
    directory(data / 'worker-agents')
    directory(data / 'worker-results')
    jobs = data / 'worker-results'
    completed = {}
    for path in jobs.glob('*.json'):
        if ID.fullmatch(path.stem):
            completed[path.stem] = load(path)
    health, leases, staging = {}, {}, {}
    expiry = 0
    pending = None
    running = True

    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with ThreadPoolExecutor(max_workers=1) as pool:
        while running:
            if pending and pending[1].done():
                job, future = pending
                result = future.result()
                completed[job['id']] = result
                save(jobs / (job['id'] + '.json'), result)
                pending = None
            try:
                adopted = {file.parent.name: load(file)['device'] for file in
                           (data / 'worker-agents').glob('*/legacy.json')}
                response = channel.json('/poll', {'completed': completed, 'agents': health, 'adopted': adopted}, timeout=12)
                leases = response['leases']
                staging = response['staging']
                expiry = time.monotonic() + min(60, response['seconds'])
                update_role_snapshots(data, response)
                if not pending and response['jobs']:
                    job = response['jobs'][0]
                    if job['id'] not in completed:
                        pending = (job, pool.submit(perform, data, worker, channel, job))
                # The peer has acknowledged every result in the request. Keep only a job it is still offering.
                keep = {item['id'] for item in response['jobs']}
                for identifier in tuple(completed):
                    if identifier not in keep:
                        (jobs / (identifier + '.json')).unlink(missing_ok=True)
                        del completed[identifier]
            except (OSError, ValueError, KeyError):
                pass
            if time.monotonic() >= expiry:
                leases = {}
                staging = {}
            if leases:
                try:
                    desktop_ready(Path(worker['desktop']), worker['coordinator'])
                except (ValueError, OSError, SubprocessError):
                    leases = {}
            health = reconcile(data, worker, leases, staging, health, pending[0]['details']['key'] if pending else None)
            time.sleep(3)
    stop_workers(data)


def stop_workers(data):
    worker = load(data / 'worker.json')
    for file in (data / 'worker-agents').glob('*/runtime.json'):
        runtime = worker_runtime(data, load(file), worker)
        try:
            runtime.command('stop', '--timeout', '20', *runtime.connectors, runtime.engine, timeout=90)
        except SubprocessError:
            continue


def configure_worker(data, key):
    worker = load(data / 'worker.json')
    details = load(installation(data, key) / 'runtime.json')
    runtime = worker_runtime(data, details, worker)
    with assignment_lock(runtime):
        if runtime.running(runtime.command, runtime.connectors):
            raise ValueError('Drain the running agent before changing its provider on this host')
        runtime.warm()
        if key == TELEGRAM:
            app = ('/opt/amplifier-acp/.venv/bin/python', '-B', '/opt/telegram/sign_in.py')
        else:
            app = ('/opt/amplifier-cli/.venv/bin/python', '-B', '/opt/office/provider_setup.py')
        runtime.command('exec', runtime.engine, *app)


def adopt(data, key, legacy):
    from remote import private_json
    from mobility_transport import secret_digest

    safe_key(key)
    worker = load(data / 'worker.json')
    legacy = legacy.resolve(strict=True)
    runtime = worker_runtime(data, {'key': key}, worker)
    state = legacy / 'state' / key
    with assignment_lock(runtime):
        if runtime.compose.is_file() or legacy == data:
            raise ValueError('Use the separate data directory of the legacy worker')
        receipt = private_json(legacy / 'receipt.json')
        device = secret_digest((state / 'matrix/matrix-token').read_text().strip())
        manifest = private_json(legacy / 'compose.yaml')
        if receipt['agent'] != key or receipt['device'] != device or manifest['name'] != 'constellation-remote-' + key:
            raise ValueError('The legacy worker and its Matrix receipt do not match')
        command = ('docker', 'compose', '--project-directory', str(legacy), '-f', str(legacy / 'compose.yaml'))
        if runtime.running(lambda *args, **options: run(*command, *args, **options), [key, 'engine']):
            raise ValueError('Wait for the legacy agent’s current turn, then stop its connector and engine before adoption')
        run(*command, 'rm', '-f', key, 'engine', timeout=90)
        save(runtime.root / 'legacy.json', {'state': str(state), 'device': device})
    print(f'{key} is parked. Start its move from the coordinator once the next worker heartbeat arrives.')
