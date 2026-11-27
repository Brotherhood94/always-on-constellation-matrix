from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from installer import PRESETS, agent_add, load, normalized, register_entry, save
from compose import definition
from roster import (archive_worker, deactivate_worker, drain_queue, enable_management, invitation_ready,
                    local_worker, prepare_management, retire_locked)
from roster_broker import handle, validate, verify, WINDOW
from persona_management import disconnect
from provision import MatrixError


AGENT = {'name': 'Fixture', 'profile': {'role': 'worker'}, 'placement': 'local', 'active': True}
SETTINGS = {'server_name': 'fixture.test', 'owner': 'operator', 'agents': ['fixture'], 'telegram': False, 'project': 'fixture',
            'roster_management': True}
BRIDGE_KEY = 'synthetic-roster-bridge-fixture'


def approved(operation, decision='approve'):
    proposal = json.dumps(operation, separators=(',', ':'))
    digest = lambda text: sha256(text.encode()).hexdigest()
    choice = json.dumps([digest('$request'), digest(proposal), '$proposal', decision], separators=(',', ':'))
    return {'key': BRIDGE_KEY, 'request': '$request', 'proposal': '$proposal', 'poll': '$poll',
            'vote': '$vote', 'operation': proposal, 'choice': digest(choice), 'decided': 1000}


class RosterTest(TestCase):
    def test_default_management_uses_prepared_invitation_and_waits_for_chief_readiness(self):
        settings = {**SETTINGS, 'systemd': True, 'roster_management': False}
        registry = {'agents': {'chief-of-staff': {'active': False}}}
        with TemporaryDirectory() as work, patch('roster.open_existing', return_value=(settings, registry)), \
             patch('roster.office_inviter') as inviter, patch('roster.password') as password, \
             patch('roster.private_secret') as secret, patch('roster.compose_config'), \
             patch('roster.up') as up, patch('roster.publish_inventory'), patch('host_services.roster_units') as units:
            data = Path(work)
            save(data / 'settings.json', settings)
            save(data / 'agents.json', registry)
            save(data / 'rooms.json', {'office': '!office:fixture.test'})

            def grant(directory, _settings, token):
                self.assertEqual(token, 'existing-operator-session')
                save(directory / 'roster/inviter-token', 'synthetic-inviter-token')

            inviter.side_effect = grant
            prepare_management(data, settings, registry, 'existing-operator-session')
            prepare_management(data, settings, registry, 'existing-operator-session')
            self.assertTrue(invitation_ready(data))
            self.assertEqual(inviter.call_count, 1)
            enable_management(data, automatic=True)
            units.assert_not_called()
            registry['agents']['chief-of-staff']['active'] = True
            save(data / 'agents.json', registry)
            up.side_effect = [OSError('restart interrupted'), None]
            with self.assertRaisesRegex(OSError, 'interrupted'):
                enable_management(data, automatic=True)
            self.assertFalse(load(data / 'settings.json')['roster_management'])
            enable_management(data, automatic=True)
            self.assertTrue(load(data / 'settings.json')['roster_management'])
            enable_management(data, automatic=True)
            self.assertEqual(units.call_count, 2)
            self.assertEqual(secret.call_count, 2)
            password.assert_not_called()

    def test_automatic_management_ignores_hosts_without_systemd_or_an_active_chief(self):
        with TemporaryDirectory() as work, patch('roster.prepare_invitation') as invite, \
             patch('roster.open_existing') as reopen:
            data = Path(work)
            for systemd, agents in [(False, {'chief-of-staff': {'active': True}}), (True, {})]:
                settings = {**SETTINGS, 'systemd': systemd}
                settings.pop('roster_management')
                registry = {'agents': agents}
                save(data / 'settings.json', settings)
                save(data / 'agents.json', registry)
                prepare_management(data, settings, registry, 'existing-operator-session')
                enable_management(data, automatic=True)
            invite.assert_not_called()
            reopen.assert_not_called()

    def test_configure_flag_registers_then_opens_native_setup(self):
        with TemporaryDirectory() as work, patch('installer.sys.stdin.isatty', return_value=True), \
             patch('installer.open_existing', return_value=(SETTINGS, {'agents': {}})), \
             patch('installer.password', return_value='operator-secret'), \
             patch('installer.login', return_value={'access_token': 'terminal-token'}), patch('installer.request'), \
             patch('installer.provision_agent', return_value='fixture') as add, patch('installer.agent_activate') as sign_in:
            data = Path(work)
            profile = data / 'profile.json'
            definition = {'key': 'fixture', 'profile': {'role': 'worker'}}
            save(profile, definition)
            agent_add(data, SimpleNamespace(profile=profile, configure=True, owner_password_file=None))
            add.assert_called_once_with(data, SETTINGS, {'agents': {}}, definition, 'terminal-token')
            sign_in.assert_called_once_with(data, 'fixture', pending_only=True)

    def test_retires_only_ordinary_local_workers_and_reserves_retired_ids(self):
        for target in ('manager', 'chief-of-staff', 'root', 'dot', 'roster-inviter', '../fixture'):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, 'ordinary local'):
                local_worker(target, AGENT)
        with self.assertRaisesRegex(ValueError, 'ordinary local'):
            local_worker('remote-fixture', {**AGENT, 'placement': 'remote'})
        with self.assertRaisesRegex(ValueError, 'retired'):
            register_entry(SETTINGS, {'agents': {}, 'retired': {'fixture': {}}}, 'fixture', {})

    def test_bridge_socket_and_key_are_mounted_only_for_chief_matrix_connector(self):
        presets = load(PRESETS)
        settings = normalized({**SETTINGS, 'agents': ['chief-of-staff', 'coder'], 'dot': True}, presets)
        registry = {'agents': {}}
        for key in settings['agents']:
            register_entry(settings, registry, key, presets[key])
        compose = definition(settings, Path('/srv/fixture'), registry, {'POSTGRES_PASSWORD': 'non-secret fixture'})
        recipients = {key for key, service in compose['services'].items() if any(
            str(item.get('target', '')).startswith(('/roster-key', '/run/office-roster.sock'))
            for item in service.get('volumes', []))}
        self.assertEqual(recipients, {'chief-of-staff'})
        self.assertTrue(all('docker.sock' not in str(service.get('volumes', [])) for service in compose['services'].values()))

    def test_queue_fences_new_work_waits_for_reactions_and_evidence(self):
        with TemporaryDirectory() as work:
            path = Path(work) / 'connector.sqlite3'
            with closing(sqlite3.connect(path)) as db:
                db.executescript('''
                    CREATE TABLE inbox (id TEXT PRIMARY KEY, sent TEXT);
                    CREATE TABLE assignment_reactions (assignment TEXT, activity_id TEXT, activity_removed INT,
                                                        outcome_key TEXT, outcome_id TEXT);
                    CREATE TABLE context_outbox (id TEXT PRIMARY KEY);
                    INSERT INTO inbox VALUES ('accepted', NULL);
                    INSERT INTO assignment_reactions VALUES ('accepted', 'eyes', 0, 'done', NULL);
                    INSERT INTO context_outbox VALUES ('evidence');
                ''')
                self.assertTrue(drain_queue(path))
                with db:
                    db.execute("INSERT OR IGNORE INTO inbox VALUES ('new', NULL)")
                    db.execute("UPDATE inbox SET sent='reply' WHERE id='accepted'")
                self.assertEqual(db.execute('SELECT id FROM inbox').fetchall(), [('accepted',)])
                self.assertTrue(drain_queue(path))
                with db:
                    db.execute("UPDATE assignment_reactions SET activity_removed=1, outcome_id='check'")
                self.assertTrue(drain_queue(path))
                with db:
                    db.execute('DELETE FROM context_outbox')
                self.assertFalse(drain_queue(path))

    def test_retirement_persists_phases_and_resumes_after_context_failure(self):
        with TemporaryDirectory() as work, patch('roster.drain_queue', side_effect=[True, False]), \
             patch('roster.compose') as compose, \
             patch('roster.compose_config', side_effect=lambda _, data, __: save(data / 'compose.yaml', {'networks': {}})), \
             patch('roster.deactivate_worker') as deactivate, patch('persona_management.disconnect') as workshop, \
             patch('roster.refresh_context', side_effect=[OSError('offline'), None]) as context, \
             patch('roster.publish_inventory'), patch('roster.sync_workshop'), patch('roster.archive_worker') as archive, \
             patch('roster.command_output', return_value=''):
            data = Path(work)
            settings = {**SETTINGS, 'agents': ['fixture']}
            registry = {'agents': {'fixture': AGENT.copy(), 'other': AGENT.copy()}}
            self.assertEqual(retire_locked(data, settings, registry, 'fixture'), 'draining')
            compose.assert_not_called()
            self.assertEqual(load(data / 'agents.json')['agents']['fixture']['retirement'], 'draining')
            with self.assertRaisesRegex(OSError, 'offline'):
                retire_locked(data, settings, registry, 'fixture')
            registry = load(data / 'agents.json')
            self.assertEqual(registry['agents']['fixture']['retirement'], 'revoked')
            self.assertEqual(retire_locked(data, settings, registry, 'fixture'), 'retired')
            self.assertEqual(retire_locked(data, settings, registry, 'fixture'), 'retired')
            deactivate.assert_called_once()
            workshop.assert_called_once()
            archive.assert_called_once_with(data, 'fixture')
            self.assertEqual(compose.call_count, 2)
            self.assertEqual(context.call_count, 2)
            self.assertNotIn('fixture', load(data / 'agents.json')['agents'])
            self.assertIn('fixture', load(data / 'agents.json')['retired'])
            self.assertEqual(load(data / 'settings.json')['agents'], [])

    def test_archive_preserves_workspace_and_sessions_and_clears_private_runtime(self):
        with TemporaryDirectory() as work, patch('uninstall.check_purge_mounts'):
            data = Path(work)
            state = data / 'state/fixture'
            for relative in ('work/output.md', 'matrix/connector.sqlite3', 'matrix/matrix-token',
                             'instance/sessions/session.json', 'instance/backend/amplifier/settings.yaml',
                             'instance/backend/acp/sessions/active/transcript.json'):
                item = state / relative
                item.parent.mkdir(parents=True, exist_ok=True)
                item.write_text(relative)
            (data / 'desktop').mkdir()
            (data / 'desktop/shared.md').write_text('shared')
            (data / 'context').mkdir()
            (data / 'context/fixture.token').write_text('revoked')
            archive_worker(data, 'fixture')
            archive_worker(data, 'fixture')
            archive = data / 'archive/agents/fixture'
            self.assertEqual({str(path.relative_to(archive)) for path in archive.rglob('*') if path.is_file()},
                             {'workspace/output.md', 'instance-sessions/session.json', 'connector.sqlite3',
                              'amplifier-sessions/active/transcript.json'})
            self.assertEqual(archive.stat().st_mode & 0o777, 0o700)
            self.assertTrue((data / 'desktop/shared.md').exists())
            self.assertFalse(state.exists())
            self.assertFalse((data / 'context/fixture.token').exists())

    def test_matrix_login_denial_after_lost_receipt_requires_deactivation_confirmation(self):
        with TemporaryDirectory() as work, patch('roster.request') as request, patch('roster.account_deactivated') as check:
            data = Path(work)
            state = data / 'state/fixture'
            (state / 'matrix').mkdir(parents=True)
            save(state / 'matrix/matrix-password', 'synthetic-worker-password')
            request.side_effect = MatrixError('POST', '/login', 403, {'errcode': 'M_FORBIDDEN'})
            check.return_value = False
            with self.assertRaises(MatrixError):
                deactivate_worker(data, 'fixture', SETTINGS)
            self.assertFalse((state / 'matrix-retired').exists())
            check.return_value = True
            deactivate_worker(data, 'fixture', SETTINGS)
            request.reset_mock()
            deactivate_worker(data, 'fixture', SETTINGS)
            request.assert_not_called()

    def test_broker_validates_the_exact_vote_digest_and_local_profile(self):
        operation = {'kind': 'retire', 'agent': 'fixture'}
        payload = approved(operation)
        self.assertEqual(validate(payload, BRIDGE_KEY, 1001)[1], operation)
        for invalid in ({**payload, 'operation': payload['operation'].replace('fixture', 'another')},
                        {**payload, 'key': 'wrong'}, approved(operation, 'reject'),
                        {**payload, 'vote': payload['poll']}):
            with self.assertRaises(ValueError):
                validate(invalid, BRIDGE_KEY, 1001)
        profile = {'key': 'fixture', 'name': 'Fixture', 'model': 'fixture-model', 'effort': 'low',
                   'profile': {'role': 'worker', 'limits': 'Review tasks.', 'capabilities': []}}
        add = {'kind': 'add', 'agent': 'fixture', 'profile': profile}
        self.assertEqual(validate(approved(add), BRIDGE_KEY, 1001)[1], add)
        for extra in ({'placement': 'remote'}, {'command': 'unapproved'}):
            with self.assertRaises(ValueError):
                validate(approved({**add, 'profile': {**profile, **extra}}), BRIDGE_KEY, 1001)

    def test_broker_verifies_room_sender_encryption_and_poll_relation(self):
        chief = '@chief-of-staff:fixture.test'
        owner = '@operator:fixture.test'
        audience = [{'type': 'm.room.member', 'state_key': user, 'content': {'membership': 'join'}}
                    for user in (chief, owner)]
        audience.extend([{'type': 'm.room.join_rules', 'content': {'join_rule': 'invite'}},
                         {'type': 'm.room.encryption', 'content': {'algorithm': 'm.megolm.v1.aes-sha2'}}])
        events = [{'event_id': event, 'origin_server_ts': stamp, 'type': 'm.room.encrypted', 'sender': sender}
                  for event, stamp, sender in (('$request', 997, owner), ('$proposal', 998, chief),
                                               ('$poll', 999, chief), ('$vote', 1000, owner))]
        events[-1]['content'] = {'m.relates_to': {'rel_type': 'm.reference', 'event_id': '$poll'}}
        payload = approved({'kind': 'retire', 'agent': 'fixture'})
        with TemporaryDirectory() as work, patch('roster_broker.request') as fetch:
            data = Path(work)
            home = data / 'state/chief-of-staff/matrix'
            home.mkdir(parents=True)
            save(home / 'room-id', '!chief:fixture.test')
            save(home / 'matrix-token', 'fixture-token')
            fetch.side_effect = [audience, *events]
            verify(data, SETTINGS, payload, 1001)
            for changed in ({'sender': '@other:fixture.test'}, {'type': 'm.room.message'},
                            {'content': {'m.relates_to': {'event_id': '$other', 'rel_type': 'm.reference'}}}):
                fetch.side_effect = [audience, *events[:-1], {**events[-1], **changed}]
                with self.assertRaises(ValueError):
                    verify(data, SETTINGS, payload, 1001)
            fetch.side_effect = [audience, *events]
            with self.assertRaisesRegex(ValueError, 'expired'):
                verify(data, SETTINGS, payload, 1001 + WINDOW)

    def test_broker_retries_partial_operation_and_reuses_completed_receipt(self):
        payload = approved({'kind': 'retire', 'agent': 'fixture'})
        registry = {'agents': {'fixture': AGENT, 'chief-of-staff': {'active': True}}}
        with TemporaryDirectory() as work, patch('roster_broker.open_existing', return_value=(SETTINGS, registry)), \
             patch('roster_broker.verify') as verify_receipt, \
             patch('roster_broker.retire_locked', side_effect=[OSError('temporary'), 'draining', 'retired']) as retire:
            data = Path(work)
            (data / 'roster').mkdir()
            save(data / 'roster/bridge-key', BRIDGE_KEY)
            with self.assertRaisesRegex(OSError, 'temporary'):
                handle(data, payload, now=1001)
            self.assertEqual(handle(data, payload, now=1002)['status'], 'draining')
            self.assertEqual(handle(data, payload, now=1003)['status'], 'retired')
            self.assertEqual(handle(data, payload, now=1004)['status'], 'retired')
            with self.assertRaisesRegex(ValueError, 'different approval'):
                handle(data, {**payload, 'vote': '$other'}, now=1004)
            verify_receipt.assert_called_once()
            self.assertEqual(retire.call_count, 3)

    def test_workshop_disconnect_is_scoped_and_idempotent(self):
        target = 'c7d6d224-eec4-4dab-8e7d-ce1c04c54495'
        record = {'id': target, 'url': 'https://engine-fixture:19443', 'agent_id': 'instance'}
        with TemporaryDirectory() as work, patch('installer.compose', return_value=subprocess.CompletedProcess(
                ['docker'], 0, stdout='workshop')), patch('persona_management.api') as api:
            data = Path(work)
            home = data / 'workshop-management'
            home.mkdir()
            save(home / 'targets.json', {'fixture': {'target': target, 'instance': 'instance'}})
            save(home / 'fixture.token', 'fixture')
            api.side_effect = [{'items': [record]}, {'ok': True}]
            disconnect(data, 'fixture')
            disconnect(data, 'fixture')
            self.assertEqual(api.call_args.args, ('workshop', 'DELETE', '/api/v1/agent-targets/' + target))
            self.assertEqual(load(home / 'targets.json'), {})
            self.assertFalse((home / 'fixture.token').exists())
