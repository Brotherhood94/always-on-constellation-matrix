from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integrations/root'))
from maintenance import Maintenance, atomic, read
from broker import MAINTENANCE_RELEASE, REVIEWED_PLAN, database, execute_plan, handle, verify_matrix_receipt

BASE = 'a' * 40
JOB = 'a' * 32
IMAGE = 'sha256:' + 'c' * 64
WORKER = {'placement': 'local', 'active': True, 'profile': {'role': 'worker'}}
PROJECT = 'maintenance-fixture'
ROOT = Path(__file__).resolve().parents[1]


class Fixtures(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name)
        self.time = 1000
        self.calls = []
        self.unhealthy = True
        self.failed_upgrade = False
        self.worker_id = 'b' * 64
        self.registry = {'agents': {'coder': WORKER, 'the-architect': WORKER, 'other': WORKER}}
        atomic(self.data / 'agents.json', self.registry)
        atomic(self.data / 'compose.yaml', {'name': PROJECT, 'services': {
            'coder': {'image': 'fixture:stable'}, 'the-architect': {'image': 'fixture:admin'},
        }})
        self.maintenance = Maintenance(self.data, runner=self.docker, clock=lambda: self.time, sleep=lambda _: None)
        instance = self.maintenance
        for directory in (instance.root, instance.drafts, instance.releases, instance.jobs):
            directory.mkdir()
        atomic(instance.root / 'active.json', {'revision': BASE, 'images': {}, 'dashboard': None})
        atomic(instance.root / 'policy.json', {'project': PROJECT, 'restart': ['coder'],
                                             'base_image': 'fixture:trusted', 'base_id': IMAGE})
        release = instance.releases / BASE
        (release / 'dashboard').mkdir(parents=True)
        for name in ('index.html', 'dashboard.css', 'dashboard.mjs', 'connections.mjs'):
            (release / 'dashboard' / name).write_text((ROOT / 'dashboard' / name).read_text())
        (release / 'agents/connector').mkdir(parents=True)
        (release / 'agents/connector/default.mjs').write_text('export const revision = 1;\n')
        (release / 'protected').mkdir()
        (release / 'protected/root.py').write_text('original\n')
        instance.dashboard(instance.active())

    def test_install_upgrade_updates_clean_maintenance_base_and_preserves_approved_releases(self):
        service = self.maintenance
        revision = '0' * 40
        def checkout(_source, target, **_):
            dashboard = target / 'dashboard'
            dashboard.mkdir()
            for name in ('index.html', 'dashboard.css', 'dashboard.mjs', 'connections.mjs'):
                (dashboard / name).write_text('updated ' + name)
            return revision
        with patch('maintenance.exported_checkout', side_effect=checkout) as export:
            service.initialize(ROOT, PROJECT, ['coder'], self.registry, update_base=True)
            self.assertEqual(service.active()['revision'], revision)
            self.assertEqual((self.data / 'dashboard/index.html').read_text(), 'updated index.html')
            prior = {**service.active(), 'images': {'coder': 'fixture:active-release'}}
            atomic(service.root / 'active.json', prior)
            service.initialize(ROOT, PROJECT, ['coder'], self.registry, update_base=True)
            self.assertEqual(service.active(), prior)
            export.assert_called_once()

    def docker(self, *args, **kwargs):
        self.calls.append(args)
        if args[1:3] == ('image', 'inspect'):
            return (IMAGE + '\n').encode()
        if 'ps' in args and args[-1] == 'coder':
            return (self.worker_id + '\n').encode()
        if args[1] == 'inspect':
            detail = {'Running': True, 'Status': 'running', 'Health': {'Status': 'unhealthy' if self.unhealthy else 'healthy'}}
            if hasattr(self, 'stopped'):
                detail = {'Running': False, 'Status': 'exited', 'OOMKilled': False}
            return json.dumps([{'Id': self.worker_id, 'Image': IMAGE, 'State': detail, 'Config': {'Labels': {
                'com.docker.compose.project': PROJECT, 'com.docker.compose.service': 'coder',
            }}}]).encode()
        if args[1] == 'restart':
            self.unhealthy = False
        if 'up' in args and self.failed_upgrade:
            self.failed_upgrade = False
            raise subprocess.CalledProcessError(1, args)
        return b''

    def candidate(self, path, before, after):
        self.maintenance.prepare(JOB)
        draft = self.maintenance.drafts / JOB / path
        draft.write_text(draft.read_text().replace(before, after))
        return self.maintenance.seal(JOB)

    def test_writable_staging_is_sealed_and_the_same_release_is_applied_once(self):
        service = self.maintenance
        review = self.candidate('dashboard/index.html', 'Constellation Dashboard', 'Fixture Dashboard')
        release = review['release']
        assert '+  <title>Fixture Dashboard</title>' in review['diff']
        self.assertEqual(sha256(review['diff'].encode()).hexdigest(), release['diff_sha256'])
        (service.drafts / JOB / 'dashboard/index.html').write_text('Draft edited after approval')
        self.assertEqual(service.seal(JOB), review)
        self.assertEqual(service.reviewed_diff(JOB), review['diff'])
        with self.assertRaisesRegex(ValueError, 'does not match'):
            service.activate(JOB, {**release, 'services': ['the-architect']})
        self.assertEqual(service.activate(JOB, release), 'completed')
        self.assertIn('Fixture Dashboard', (self.data / 'dashboard/index.html').read_text())
        self.assertIn('releases/' + release['revision'], (self.data / 'dashboard/index.html').read_text())
        self.assertEqual(read(self.data / 'dashboard/maintenance.json')['revision'], release['revision'])
        self.assertEqual(service.activate(JOB, release), 'completed')
        run = next(a for a in self.calls if a[1] == 'run')
        self.assertIn('--network=none', run)
        self.assertIn('--read-only', run)
        self.assertFalse(any('docker.sock' in part or str(self.data) in part for part in run))

    def test_export_is_a_git_patch_even_without_a_final_newline(self):
        service = self.maintenance
        service.prepare(JOB)
        source = service.releases / BASE
        name = 'agents/connector/default.mjs'
        (service.drafts / JOB / name).write_text('export const revision = 2;')
        review = service.seal(JOB)
        patch = service.reviewed_diff(JOB)
        self.assertIn('\\ No newline at end of file\n', patch)
        subprocess.run(('git', 'apply', '--check', '-'), cwd=source, input=patch, text=True, check=True)
        self.assertEqual(sha256(patch.encode()).hexdigest(), review['release']['diff_sha256'])

    def test_protected_and_symlink_drafts_are_rejected(self):
        service = self.maintenance
        service.prepare(JOB)
        draft = service.drafts / JOB
        (draft / 'protected/root.py').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'from the VPS terminal'):
            service.seal(JOB)
        (draft / 'protected/root.py').unlink()
        (draft / 'protected/root.py').symlink_to('/etc/passwd')
        with self.assertRaisesRegex(ValueError, 'regular'):
            service.seal(JOB)

    def test_connector_health_failure_restores_the_pinned_image(self):
        service = self.maintenance
        self.unhealthy = False
        review = self.candidate('agents/connector/default.mjs', '1;', '2;')
        self.assertEqual(review['release']['services'], ['coder'])
        self.failed_upgrade = True
        self.assertEqual(service.activate(JOB, review['release']), 'rolled_back')
        self.assertEqual(service.active()['revision'], BASE)
        image = service.manifest()['services']['coder']['image']
        self.assertEqual(image, service.image_tag('rollback', IMAGE.split(':')[1]))
        self.assertEqual(service.manifest()['services']['the-architect']['image'], 'fixture:admin')
        self.assertEqual(len([a for a in self.calls if 'up' in a]), 2)

    def test_interrupted_apply_rolls_back_and_a_stale_base_cannot_deploy(self):
        service = self.maintenance
        review = self.candidate('dashboard/index.html', 'Constellation Dashboard', 'Fixture Dashboard')
        release = review['release']
        baseline = service.active()
        atomic(service.root / 'active.json', {**baseline, 'revision': 'f' * 40})
        self.assertEqual(service.activate(JOB, release), 'file_changed')
        journal = service.jobs / (JOB + '.release.json')
        atomic(journal, {'status': 'applying', 'original': {}, 'before': baseline, 'after': release})
        self.assertEqual(service.recover_release(JOB), 'rolled_back')
        self.assertEqual(service.recover_release(JOB), 'rolled_back')
        atomic(journal, {**read(journal), 'status': 'recovery_required'})
        self.assertTrue(service.incomplete_releases())
        self.assertEqual(service.recover_release(JOB, retry=True), 'rolled_back')
        self.assertFalse(service.incomplete_releases())
        self.assertEqual(service.active(), baseline)

    def test_recovery_rechecks_faults_and_respects_pause_health_and_cooldown(self):
        service = self.maintenance
        self.assertEqual(service.scan(), [])
        self.time += 29
        self.assertEqual(service.scan(), [])
        self.time += 1
        receipt = service.scan()[0]
        self.assertEqual((receipt['service'], receipt['reason'], receipt['status']), ('coder', 'unhealthy', 'healthy'))
        self.time += 31
        self.unhealthy = True
        service.scan()
        self.assertEqual(len(service.scan()), 1)
        self.time += 901
        self.stopped = True
        self.assertEqual(len(service.scan()), 1)
        del self.stopped
        atomic(service.root / 'paused', 'pause')
        service.scan()
        self.assertEqual(len([a for a in self.calls if a[1] == 'restart']), 1)

    def test_telegram_restart_requires_operator_vote_for_the_bridge_and_defers_uncertain_delivery(self):
        audit, office = '!audit:fixture.test', '!office:fixture.test'
        agent, owner, architect = ('@telegram-agent:fixture.test', '@operator:fixture.test', '@the-architect:fixture.test')
        config = {'audit': audit, 'agents': office, 'owner': owner, 'root': architect, 'control_audience': [owner, architect, agent]}
        exact = {'kind': 'restart-service', 'target': 'telegram-bridge'}
        reference = {'origin_server_ts': 4, 'sender': owner,
                     'content': {'m.relates_to': {'rel_type': 'm.reference', 'event_id': '$poll'}}}
        events = {'$request': {'sender': agent, 'origin_server_ts': 1}, '$proposal': {'sender': architect, 'origin_server_ts': 2},
                  '$poll': {'sender': architect, 'origin_server_ts': 3}, '$vote': reference}
        receipt = {'id': sha256(f'{audit}:$request'.encode()).hexdigest()[:32], 'operation': REVIEWED_PLAN, 'plan': json.dumps(exact),
                   'source_room': audit, 'review_room': audit, 'request': '$request', 'proposal': '$proposal',
                   'poll': '$poll', 'decision': '$vote', 'decided': 4, 'expires': 100}
        verify_matrix_receipt(config, receipt, lambda _, event: events[event])
        with self.assertRaises(ValueError):
            verify_matrix_receipt(config, {**receipt, 'plan': json.dumps({**exact, 'target': 'synapse'})}, lambda _, event: events[event])
        events['$vote']['sender'] = agent
        with self.assertRaises(ValueError):
            verify_matrix_receipt(config, receipt, lambda _, event: events[event])

        connector = self.data / 'telegram/connector'
        connector.mkdir(parents=True)
        connection = sqlite3.connect(connector / 'telegram.sqlite3')
        self.addCleanup(connection.close)
        with connection:
            connection.execute("CREATE TABLE draft (state TEXT)")
            connection.execute("INSERT INTO draft VALUES ('unknown')")
        state = self.data / 'state/the-architect/execution'
        with patch('service_diagnostics.restart_service', return_value=('completed', 0, {})) as restart:
            denied = execute_plan(json.dumps(exact), state, JOB)
            self.assertEqual(denied[0], 'precondition_failed')
            restart.assert_not_called()
            self.assertFalse((connector / 'bridge-restarting').exists())
            with connection:
                connection.execute("UPDATE draft SET state='bridge_confirmed'")
            self.assertEqual(execute_plan(json.dumps(exact), state, JOB)[0], 'completed')
            restart.assert_called_once()
            self.assertFalse((connector / 'bridge-restarting').exists())

    def test_broker_applies_only_a_verified_approval(self):
        service = self.maintenance
        release = self.candidate('dashboard/index.html', 'Constellation Dashboard', 'Fixture dashboard')['release']
        plan = json.dumps(release)
        digest = sha256(plan.encode()).hexdigest()
        file = self.data / 'approval.sqlite'
        db = database(file)
        self.addCleanup(db.close)
        core = {'id': JOB, 'request': '$request', 'decision': '$vote', 'proposal': '$proposal', 'poll': '$poll',
                'approver': '@operator:fixture.test', 'hash': digest, 'operation': MAINTENANCE_RELEASE, 'plan': plan,
                'decided': 1000, 'expires': 2000, 'action': 'approve'}
        core['choice'] = sha256(json.dumps([JOB, digest, '$proposal', 'approve'], separators=(',', ':')).encode()).hexdigest()
        checked = []
        with self.assertRaises(ValueError):
            handle(db, core, {core['approver']}, {}, now=1100, maintenance=service)
        self.assertEqual(service.active()['revision'], BASE)
        kwargs = {'now': 1100, 'maintenance': service, 'verify_review': checked.append, 'require_review': True}
        self.assertEqual(handle(db, core, {core['approver']}, {}, **kwargs)['outcome'], 'completed')
        self.assertEqual(handle(db, core, {core['approver']}, {}, **kwargs)['outcome'], 'completed')
        self.assertEqual(len(checked), 1)


if __name__ == '__main__':
    unittest.main()
