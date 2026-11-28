"""Host-side staging, releases and scoped Docker recovery for The Architect."""

from contextlib import contextmanager
from difflib import unified_diff
import fcntl
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
from tempfile import NamedTemporaryFile
import time

ASSETS = ('index.html', 'dashboard.css', 'dashboard.mjs', 'connections.mjs')
MANAGERS = {'the-architect', 'chief-of-staff', 'hiring-manager', 'coach', 'scouter', 'root', 'helper'}
SERVICE = re.compile(r'[a-z0-9][a-z0-9_-]{0,35}')
JOB = re.compile(r'[a-f0-9]{32}')
REV = re.compile(r'[a-f0-9]{40,64}')
IMAGE = re.compile(r'sha256:[a-f0-9]{64}')
CONNECTOR = re.compile(r'agents/connector/[a-zA-Z0-9_-]+\.mjs')
FIXED_ENV = {'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'}
MAX_TEXT = 256 * 1024
UNSAFE_REVIEW_TEXT = re.compile(r'[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]')
MAX_DIFF = 5000
COOLDOWN = 15 * 60
MAX_LOG = 100


def stamp():
    return int(time.time())


def read(path, default=None):
    if not path.exists() and default is not None:
        return default
    return json.loads(path.read_text())


def atomic(path, value, mode=0o600):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False) + '\n'
    with NamedTemporaryFile(dir=path.parent, delete=False, encoding='utf-8', mode='w') as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), mode)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            temporary.unlink(missing_ok=True)


def run(*argv, timeout=60, cwd=None, env=None):
    result = subprocess.run(argv, cwd=cwd, env=env or FIXED_ENV, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=timeout, check=True)
    return result.stdout


def ordinary(name, registry, *, require_active=True):
    entry = registry.get('agents', {}).get(name, {})
    return bool(SERVICE.fullmatch(name) and name not in MANAGERS and entry.get('profile', {}).get('role') in {'worker', 'coordinator'}
                and (entry.get('active') or not require_active) and entry.get('placement') == 'local' and not any(
                    entry.get(key) for key in ('retirement', 'mobility_move', 'mobility_fenced')))


def exported_checkout(source, target, *, runner=run):
    env = {**FIXED_ENV, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
    git = ('/usr/bin/git', '-c', f'safe.directory={source}')
    revision = runner(*git, 'rev-parse', 'HEAD', cwd=source, env=env).decode().strip()
    if not REV.fullmatch(revision):
        raise ValueError('Use a committed Constellation checkout.')
    content = runner(*git, 'archive', '--format=tar', revision, cwd=source, env=env, timeout=60)
    with tarfile.open(fileobj=io.BytesIO(content)) as archive:
        members = archive.getmembers()
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts or not (member.isdir() or member.isreg()):
                raise ValueError('The checkout must contain only regular files and directories.')
        archive.extractall(target, members=members, filter='data')
    return revision


def opened_bytes(root, name, limit):
    """Open each component relative to a directory descriptor to defeat symlink swaps."""
    parts = Path(name).parts
    if not parts or name.startswith('/') or '..' in parts:
        raise ValueError('Invalid workspace path.')
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        with os.fdopen(os.open(parts[-1], flags, dir_fd=fd), 'rb') as file:
            before = os.fstat(file.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                raise ValueError('Use bounded regular UTF-8 files.')
            data = file.read(limit + 1)
            after = os.fstat(file.fileno())
            if (len(data) > limit or before.st_mtime_ns != after.st_mtime_ns
                    or before.st_ctime_ns != after.st_ctime_ns):
                raise ValueError('Freeze the draft before submitting it.')
            return data
    finally:
        os.close(fd)


def proposed_changes(base, draft):
    files = {}
    diff = []
    for relative in sorted(set(p.relative_to(base).as_posix() for p in base.rglob('*') if p.is_file())
                           | set(p.relative_to(draft).as_posix() for p in draft.rglob('*') if not p.is_dir() or p.is_symlink())):
        supported = relative in {f'dashboard/{asset}' for asset in ASSETS} or CONNECTOR.fullmatch(relative)
        before = base / relative
        after = draft / relative
        if not supported:
            if (not before.is_file()
                    or opened_bytes(draft, relative, before.stat().st_size) != before.read_bytes()):
                raise ValueError('Apply privileged or other unsupported changes from the VPS terminal.')
            continue
        if not before.is_file() or not after.is_file():
            raise ValueError('A live release can edit existing application files.')
        old = opened_bytes(base, relative, MAX_TEXT).decode()
        revised = opened_bytes(draft, relative, MAX_TEXT).decode()
        if UNSAFE_REVIEW_TEXT.search(revised):
            raise ValueError('Use text application files without hidden control characters.')
        if old == revised:
            continue
        files[relative] = revised
        for line in unified_diff(old.splitlines(True), revised.splitlines(True), 'a/' + relative, 'b/' + relative):
            diff.append(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n')
    result = ''.join(diff)
    if not files or len(result.encode()) > MAX_DIFF:
        raise ValueError('Submit a nonempty patch under 5,000 bytes.')
    return files, result


class Maintenance:
    def __init__(self, data, *, runner=run, clock=stamp, sleep=time.sleep, worker_uid=991):
        self.data = data
        self.root = data / 'maintenance'
        self.drafts = self.root / 'drafts'
        self.releases = self.root / 'releases'
        self.jobs = self.root / 'jobs'
        self.runner = runner
        self.clock = clock
        self.sleep = sleep
        self.worker_uid = worker_uid

    @contextmanager
    def locked(self):
        with (self.data / '.operator.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield

    def manifest(self):
        return read(self.data / 'compose.yaml')

    def config(self):
        return read(self.root / 'policy.json')

    def active(self):
        return read(self.root / 'active.json')

    def initialize(self, source, project, allow, registry, *, update_base=False):
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', project):
            raise ValueError('Use a valid Compose project for maintenance.')
        if not (self.root / 'policy.json').exists() and any(not ordinary(key, registry, require_active=False) for key in allow):
            raise ValueError('Choose registered local Office connectors for autonomous restart.')
        for name in (self.root, self.drafts, self.releases, self.jobs):
            name.mkdir(mode=0o711 if name in (self.root, self.drafts) else 0o700, exist_ok=True)
        base_path = self.root / 'active.json'
        active = self.active() if base_path.exists() else None
        use_checkout = active is None or (update_base and not active['images'] and not active['dashboard']
                                          and not self.incomplete_releases())
        if use_checkout:
            export = self.releases / 'initial'
            if export.exists():
                shutil.rmtree(export)
            export.mkdir()
            revision = exported_checkout(source, export, runner=self.runner)
            tree = self.releases / revision
            if tree.exists():
                shutil.rmtree(export)
            else:
                export.rename(tree)
            trusted = self.image_id('constellation-office-connector:0.22.17')
            tag = self.image_tag('base', trusted.split(':')[1])
            self.runner('/usr/bin/docker', 'image', 'tag', trusted, tag)
        else:
            prior = self.config()
            trusted, tag = prior['base_id'], prior['base_image']
        atomic(self.root / 'policy.json', {'project': project, 'restart': sorted(set(allow)), 'base_image': tag, 'base_id': trusted})
        if use_checkout:
            atomic(base_path, {'revision': revision, 'images': {}, 'dashboard': None})
        self.dashboard(self.active())

    @staticmethod
    def image_tag(kind, identity):
        return f'constellation-maintenance-{kind}:{identity}'

    def image_id(self, tag):
        output = self.runner('/usr/bin/docker', 'image', 'inspect', '--format', '{{.Id}}', tag).decode().strip()
        if not IMAGE.fullmatch(output):
            raise ValueError('Docker did not return a trusted image identity.')
        return output

    def job(self, identifier):
        if not isinstance(identifier, str) or not JOB.fullmatch(identifier):
            raise ValueError('Use the identity of the Office request.')
        return self.jobs / (identifier + '.json')

    def prepare(self, identifier):
        job = self.job(identifier)
        if not job.exists():
            base = self.active()['revision']
            workspace = self.drafts / identifier
            if workspace.exists():
                shutil.rmtree(workspace)
            shutil.copytree(self.releases / base, workspace)
            if os.geteuid() == 0:
                for path in [workspace, *workspace.rglob('*')]:
                    os.chown(path, self.worker_uid, self.worker_uid, follow_symlinks=False)
            workspace.chmod(0o700)
            atomic(job, {'base': base})
        return {'base': read(job)['base'], 'workspace': identifier}

    def build(self, revision, tree, *, testing=False):
        policy = self.config()
        if self.image_id(policy['base_image']) != policy['base_id']:
            raise ValueError('The trusted connector base image changed.')
        output = self.image_tag('test' if testing else 'app', revision)
        if testing:
            dockerfile = (
                'FROM ' + policy['base_image'] + ' AS base\nFROM base\n'
                'COPY agents/connector/*.mjs /candidate/agents/connector/\n'
                'COPY --from=base /app/node_modules /candidate/agents/connector/node_modules\n'
                'COPY agents/tests /candidate/agents/tests\nCOPY tests /candidate/tests\n'
                'COPY deploy/matrix/bot /candidate/deploy/matrix/bot\nCOPY dashboard /candidate/dashboard\n'
            )
        else:
            dockerfile = 'FROM ' + policy['base_image'] + '\nCOPY agents/connector/*.mjs /app/\n'
        path = tree / '.constellation-maintenance.Dockerfile'
        atomic(path, dockerfile)
        try:
            self.runner('/usr/bin/docker', 'build', '--network=none', '--pull=false', '-f', str(path),
                        '-t', output, str(tree), timeout=180)
        finally:
            path.unlink(missing_ok=True)
        return output

    def test(self, image):
        script = """
const fs = require('node:fs');
const { spawnSync } = require('node:child_process');
for (const dir of ['dashboard', 'agents/connector']) {
  for (const name of fs.readdirSync(dir)) {
    if (name.endsWith('.mjs') && spawnSync('node', ['--check', dir + '/' + name]).status !== 0) process.exit(1);
  }
}
const html = fs.readFileSync('dashboard/index.html', 'utf8');
if (!html.includes('href="dashboard.css"') || !html.includes('src="dashboard.mjs"')) process.exit(1);
const result = spawnSync('node', ['--test', 'tests/dashboard.test.mjs', 'agents/tests/manager.mjs', 'agents/tests/capability-routing.mjs'], {stdio:'inherit'});
process.exit(result.status ?? 1);
"""
        self.runner('/usr/bin/docker', 'run', '--rm', '--pull=never', '--network=none', '--read-only',
                    '--cap-drop=ALL', '--security-opt=no-new-privileges:true', '--user=991:991', '--pids-limit=64',
                    '--memory=256m', '--cpus=1', '--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=32m', '-w', '/candidate',
                    '--entrypoint=node', image, '-e', script, timeout=90)

    def seal(self, identifier):
        path = self.job(identifier)
        job = read(path)
        if 'review' in job:
            return job['review']
        active = self.active()
        if active['revision'] != job['base']:
            raise ValueError('The active revision changed. Start a new Office request.')
        parent = self.releases / job['base']
        try:
            files, diff = proposed_changes(parent, self.drafts / identifier)
        except OSError as error:
            raise ValueError('Keep the exported workspace as regular files and directories.') from error
        revision = sha256(json.dumps([job['base'], files], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        tree = self.releases / revision
        if not tree.exists():
            stage = self.releases / (revision + '.staging')
            if stage.exists():
                shutil.rmtree(stage)
            shutil.copytree(parent, stage)
            for name, content in files.items():
                (stage / name).write_text(content)
            stage.rename(tree)
        image = self.build(revision, tree, testing=True)
        try:
            self.test(image)
        except subprocess.SubprocessError as error:
            raise ValueError('The isolated syntax, routing or dashboard tests failed.') from error
        dashboard = any(name.startswith('dashboard/') for name in files)
        registry = read(self.data / 'agents.json')
        connector = any(name.startswith('agents/connector/') for name in files)
        services = sorted(name for name in self.manifest()['services'] if connector and ordinary(name, registry))
        if connector and not services:
            raise ValueError('There are no eligible running Office connectors to update.')
        release = {'base': job['base'], 'revision': revision, 'diff_sha256': sha256(diff.encode()).hexdigest(),
                   'services': services, 'dashboard': dashboard}
        job['review'] = {'release': release, 'diff': diff, 'tests': 'Connector and dashboard syntax, routing, dashboard fixtures'}
        atomic(path, job)
        return job['review']

    def reviewed_diff(self, identifier):
        path = self.job(identifier)
        review = read(path, {}).get('review')
        if not review or sha256(review['diff'].encode()).hexdigest() != review['release']['diff_sha256']:
            raise ValueError('No verified repository diff is available for this operation.')
        return review['diff']

    def compose(self, *arguments, manifest=None, timeout=60):
        base = self.data / 'compose.yaml'
        return self.runner('/usr/bin/docker', 'compose', '--project-directory', str(self.data), '--profile', 'agents',
                           '-f', str(manifest or base), *arguments, timeout=timeout)

    def inspected(self, service, *, project=None):
        ids = self.compose('ps', '--all', '--quiet', service, timeout=15).decode().split()
        if len(ids) != 1 or not re.fullmatch(r'[a-f0-9]{12,64}', ids[0]):
            return None
        info = json.loads(self.runner('/usr/bin/docker', 'inspect', ids[0], timeout=15))[0]
        labels = info.get('Config', {}).get('Labels') or {}
        if (labels.get('com.docker.compose.service') != service or
                labels.get('com.docker.compose.project') != (project or self.config()['project'])):
            return None
        return info

    def dashboard(self, active):
        directory = self.data / 'dashboard'
        directory.mkdir(exist_ok=True)
        path = active.get('dashboard')
        if path:
            root = self.releases / path / 'dashboard'
            assets = directory / 'releases'
            assets.mkdir(exist_ok=True, mode=0o755)
            assets = assets / path
            assets.mkdir(exist_ok=True, mode=0o755)
            for name in ASSETS[1:]:
                atomic(assets / name, (root / name).read_text(), mode=0o644)
            html = (root / 'index.html').read_text()
            for name in ('dashboard.css', 'dashboard.mjs'):
                html = html.replace(f'"{name}"', f'"releases/{path}/{name}"')
            atomic(directory / 'index.html', html, mode=0o644)
        else:
            for name in ASSETS:
                original = self.releases / active['revision'] / 'dashboard' / name
                atomic(directory / name, original.read_text(), mode=0o644)
        atomic(directory / 'maintenance.json', {'revision': active['revision']}, mode=0o644)

    def activate(self, identifier, release):
        job = read(self.job(identifier))
        if job.get('review', {}).get('release') != release:
            raise ValueError('The vote does not match the tested release.')
        journal = self.jobs / (identifier + '.release.json')
        if journal.exists():
            record = read(journal)
            if record['status'] == 'applying':
                return self.rollback(journal, record)
            return record['status']
        if self.incomplete_releases():
            return 'recovery_required'
        before = self.active()
        if before['revision'] != release['base']:
            return 'file_changed'
        registry = read(self.data / 'agents.json')
        services = release['services']
        original_ids = {}
        for name in services:
            info = self.inspected(name)
            if (not ordinary(name, registry) or not info or not IMAGE.fullmatch(info.get('Image', ''))
                    or (info.get('State', {}).get('Health') or {}).get('Status') != 'healthy'):
                return 'precondition_failed'
            original_ids[name] = info['Image']
        after = {**before, 'revision': release['revision'], 'images': dict(before['images'])}
        original = {}
        if services:
            image = self.build(release['revision'], self.releases / release['revision'])
            self.image_id(image)
            for name in services:
                old_id = original_ids[name]
                previous = self.image_tag('rollback', old_id.split(':')[1])
                self.runner('/usr/bin/docker', 'image', 'tag', old_id, previous)
                original[name] = previous
                after['images'][name] = image
            before = {**before, 'images': {**before['images'], **original}}
        if release['dashboard']:
            after['dashboard'] = release['revision']
        record = {'status': 'applying', 'before': before, 'after': after, 'original': original}
        atomic(journal, record)
        try:
            self.switch_images({name: after['images'][name] for name in services})
            self.dashboard(after)
            atomic(self.root / 'active.json', after)
            atomic(journal, {**record, 'status': 'completed'})
        except (OSError, subprocess.SubprocessError, ValueError):
            return self.rollback(journal, record)
        return 'completed'

    def switch_images(self, images):
        if not images:
            return
        current = self.data / 'compose.yaml'
        manifest = self.manifest()
        for key, image in images.items():
            manifest['services'][key]['image'] = image
        pending = self.root / 'pending-compose.json'
        atomic(pending, manifest)
        self.compose('config', '--quiet', manifest=pending)
        self.compose('up', '-d', '--no-deps', '--pull', 'never', '--wait', '--wait-timeout', '90',
                     *sorted(images), manifest=pending, timeout=115)
        for service, image in images.items():
            actual = self.inspected(service)
            if (not actual or actual['Image'] != self.image_id(image)
                    or (actual['State'].get('Health') or {}).get('Status') != 'healthy'):
                raise ValueError('Docker could not confirm the expected connector image and health.')
        atomic(current, manifest)
        pending.unlink(missing_ok=True)

    def rollback(self, journal, record):
        try:
            self.switch_images(record['original'])
            self.dashboard(record['before'])
            atomic(self.root / 'active.json', record['before'])
            status = 'rolled_back'
        except (OSError, subprocess.SubprocessError, ValueError):
            status = 'recovery_required'
        atomic(journal, {**record, 'status': status})
        return status

    def incomplete_releases(self):
        return any(read(journal)['status'] in {'applying', 'recovery_required'}
                   for journal in self.jobs.glob('*.release.json'))

    def recover_release(self, identifier, *, retry=False):
        journal = self.job(identifier).with_suffix('.release.json')
        if not journal.exists():
            return None
        record = read(journal)
        retryable = {'applying', 'recovery_required'} if retry else {'applying'}
        return self.rollback(journal, record) if record['status'] in retryable else record['status']

    def scan(self):
        policy = self.config()
        path = self.root / 'recovery.json'
        state = read(path, {'services': {}, 'receipts': []})
        if (self.root / 'paused').exists() or self.incomplete_releases():
            return state['receipts']
        registry = read(self.data / 'agents.json')
        unit = policy['project'] + '-stack.service'
        if not read(self.data / 'settings.json', {}).get('control_runtime'):
            try:
                self.runner('/usr/bin/systemctl', 'is-active', '--quiet', unit, timeout=5)
            except subprocess.CalledProcessError:
                return state['receipts']
        now = self.clock()
        for name in policy['restart']:
            observed = state['services'].setdefault(name, {})
            if not ordinary(name, registry) or (self.data / 'state' / name / 'matrix/relocation-drain').exists():
                observed.pop('first', None)
                continue
            info = self.inspected(name, project=policy['project'])
            detail = info.get('State', {}) if info else {}
            unhealthy = detail.get('Running') and (detail.get('Health') or {}).get('Status') == 'unhealthy'
            oom = detail.get('Status') == 'exited' and detail.get('OOMKilled') is True
            if not (unhealthy or oom):
                observed.pop('first', None)
                continue
            previous = observed.setdefault('first', {'time': now, 'id': info['Id'], 'reason': 'unhealthy' if unhealthy else 'oom'})
            if previous['id'] != info['Id'] or previous['reason'] != ('unhealthy' if unhealthy else 'oom'):
                observed['first'] = {'time': now, 'id': info['Id'], 'reason': 'unhealthy' if unhealthy else 'oom'}
                continue
            if now - previous['time'] < 30 or now - observed.get('attempted', -COOLDOWN) < COOLDOWN:
                continue
            if not re.fullmatch(r'[a-f0-9]{64}', info['Id']):
                continue
            receipt = {'id': f'{name}-{now}', 'service': name, 'time': now, 'reason': previous['reason'],
                       'status': 'unconfirmed'}
            state['receipts'] = [*state['receipts'][-(MAX_LOG - 1):], receipt]
            observed['attempted'] = now
            atomic(path, state)
            try:
                self.runner('/usr/bin/docker', 'restart', '--time', '10', info['Id'], timeout=25)
                for _ in range(12):
                    check = self.inspected(name)
                    if check and check.get('Id') == info['Id'] and check['State'].get('Health', {}).get('Status') == 'healthy':
                        receipt['status'] = 'healthy'
                        break
                    self.sleep(5)
                else:
                    receipt['status'] = 'unhealthy'
            except (OSError, subprocess.SubprocessError, ValueError):
                receipt['status'] = 'failed'
            atomic(path, state)
            break
        atomic(path, state)
        return state['receipts']
