from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from compose import definition
import hiring
from installer import available_profiles, load, normalized, save
from mobility_transport import authorize_inference, secret_digest
from roster_broker import WINDOW, handle

ACCOUNT = 'a' * 24
APPROVAL = sha256(b'$request').hexdigest()
KEY = 'synthetic-hiring-broker-key'
OPERATION = {'kind': 'add', 'agent': 'researcher', 'profile': {'key': 'researcher', 'name': 'Researcher',
    'model': 'research-fixture', 'effort': 'high', 'profile': {'role': 'worker', 'limits': 'Use public sources.',
    'capabilities': [{'code': 'research', 'description': 'Research topics.', 'resources': ['task:*']}] }},
    'provider': {'mode': 'chatgpt_codex', 'account': ACCOUNT, 'label': 'Fixture Codex'},
    'tools': [], 'firstTask': 'Compare two public sources.'}
AUTHORITY = {'active': True, 'starter_role': 'hiring-manager', 'placement': 'local'}
SETTINGS = {'server_name': 'fixture.test', 'owner': 'operator', 'roster_management': True, 'telegram': False}


class HiringTests(TestCase):
    def test_approved_hire_is_saved_before_work_and_recovers_after_vote_expiry(self):
        with TemporaryDirectory() as folder:
            data = Path(folder)
            registry = {'team_version': 2, 'agents': {'hiring-manager': AUTHORITY}}
            (data / 'roster').mkdir()
            save(data / 'roster/hiring-manager.key', KEY)
            save(data / 'settings.json', SETTINGS)
            save(data / 'agents.json', registry)
            operation = json.dumps(OPERATION, separators=(',', ':'))
            proof = json.dumps([APPROVAL, sha256(operation.encode()).hexdigest(), '$proposal', 'approve'], separators=(',', ':'))
            payload = {'actor': 'hiring-manager', 'key': KEY, 'request': '$request', 'proposal': '$proposal',
                       'poll': '$poll', 'vote': '$vote', 'decided': 1000, 'operation': operation,
                       'choice': sha256(proof.encode()).hexdigest()}
            with patch('roster_broker.open_existing', return_value=(SETTINGS, registry)), \
                    patch('roster_broker.verify') as verify, patch('hiring.open_existing', return_value=(SETTINGS, registry)), \
                    patch('hiring.advance', side_effect=['provider_waiting', 'active']) as work, \
                    patch('hiring.time.time', return_value=1000.) as clock:
                self.assertEqual(handle(data, payload, now=1000)['status'], 'activating')
                work.assert_not_called()
                hiring.reconcile(data)
                self.assertEqual(handle(data, payload, now=1001 + WINDOW)['status'], 'provider_waiting')
                clock.return_value += 31
                hiring.reconcile(data)
                hiring.reconcile(data)
                self.assertEqual(handle(data, payload, now=1001 + WINDOW)['status'], 'active')
                self.assertEqual(work.call_count, 2)
                verify.assert_called_once()
                self.assertEqual(load(data / 'agents.json'), registry)
                changed = {**payload, 'operation': operation.replace('research-fixture', 'different-model')}
                with self.assertRaises(ValueError):
                    handle(data, changed, now=1005)

    def test_chatgpt_enrollment_requires_a_recent_encrypted_operator_choice(self):
        with TemporaryDirectory() as folder:
            data = Path(folder)
            (data / 'roster').mkdir()
            save(data / 'roster/hiring-manager.key', KEY)
            registry = {'team_version':2, 'agents':{'hiring-manager':AUTHORITY}}
            choice = {'action':'begin','mode':'chatgpt_codex','event':'$choice'}
            payload = {'actor':'hiring-manager','key':KEY,'hiring':choice}
            room = '!review:fixture.test'
            owner = '@operator:fixture.test'
            source = {'event_id':'$choice','sender':owner,'type':'m.room.encrypted','origin_server_ts':1000}
            with patch('roster_broker.open_existing', return_value=(SETTINGS, registry)), \
                    patch('roster_broker.private_room', return_value=('@hiring-manager:fixture.test', owner, room,
                                                                     'fixture-matrix-token', '/room-path')), \
                    patch('roster_broker.request', return_value=source) as fetch, \
                    patch('hiring.control_request', return_value={'id':'fixture'}) as start:
                self.assertEqual(handle(data, payload, now=1005), {'id':'fixture'})
                self.assertEqual(start.call_args.args[1]['mode'], 'chatgpt_codex')
                fetch.return_value = {**source, 'sender':'@another:fixture.test'}
                with self.assertRaises(ValueError):
                    handle(data, payload, now=1005)
                fetch.return_value = source
                with self.assertRaises(ValueError):
                    handle(data, payload, now=1001 + WINDOW)
                start.assert_called_once()

    def test_verified_activation_remains_inactive_after_a_failed_tool_and_retries_the_same_engine(self):
        active = {'id': ACCOUNT, 'mode': 'chatgpt_codex', 'ready': True}
        with TemporaryDirectory() as folder:
            data = Path(folder)
            (data / 'state' / 'researcher').mkdir(parents=True)
            (data / 'roster').mkdir()
            save(data / 'roster/inviter-token', 'fixture-token')
            save(data / 'rooms.json', {'office':'!sealed:fixture.test'})
            settings = {**SETTINGS, 'tailscale': False}
            registry = {'agents': {}}
            operation = {**OPERATION, 'tools': [{'id': 'read-web'}]}
            with patch('hiring.key_for', return_value='b' * 64), patch('hiring.planned_grants', return_value=True), \
                    patch('hiring.ensure_coordinator', return_value={'accounts': [active]}), \
                    patch('hiring.control', return_value={'status': 'granted'}), patch('hiring.compose_config'), \
                    patch('hiring.refresh_context'), patch('hiring.prepare_agent_state') as register, \
                    patch('hiring.compose') as compose, patch('hiring.up'), patch('hiring.publish_inventory'), \
                    patch('hiring.commit_registry', side_effect=lambda path, state, **_: save(path, state)), \
                    patch('hiring.sync_workshop', return_value={'researcher': {'available': True}}):
                failed = True

                def run(_data, *args, **_):
                    if 'exec' in args and failed:
                        raise subprocess.CalledProcessError(1, args)

                compose.side_effect = run
                self.assertEqual(hiring.advance(data, settings, registry, operation, APPROVAL), 'tools_waiting')
                self.assertFalse(load(data / 'agents.json')['agents']['researcher']['active'])
                failed = False
                self.assertEqual(hiring.advance(data, settings, registry, operation, APPROVAL), 'active')
                self.assertEqual(hiring.advance(data, settings, registry, operation, APPROVAL), 'active')
                register.assert_called_once()
                worker = load(data / 'agents.json')['agents']['researcher']
                self.assertTrue(worker['active'])
                self.assertEqual(worker['enabled_tools'], {'read-web': {'id': 'read-web'}})

    def test_coordinator_and_roster_mounts_are_separate_from_workers(self):
        settings = normalized({**SETTINGS, 'agents': ['hiring-manager', 'coder']}, available_profiles())
        entry = {'name':'Researcher', 'model':'fixture-model', 'effort':'high', 'placement':'local', 'profile':{'limits':'Research.'},
                 'hiring_provider': {'mode':'chatgpt_plan', 'account':ACCOUNT}}
        registry = {'agents': {'researcher': entry}}
        config = definition(settings, Path('/srv/fixture'), registry, {'POSTGRES_PASSWORD':'synthetic'})
        services = config['services']
        self.assertTrue(config['networks']['hiring-models']['internal'])
        self.assertEqual(services['hiring-provider']['ports'], ['127.0.0.1:18792:8081'])
        self.assertIn('hiring-models', services['engine-researcher']['networks'])
        self.assertNotIn('hiring-models', services['researcher']['networks'])
        self.assertNotIn('/hiring', [part['target'] for part in services['engine-researcher']['volumes']])
        self.assertTrue(all('/var/run/docker.sock' not in str(item) for item in services.values()))

    def test_host_lease_revocation_rejects_the_previous_host_and_allows_only_fenced_staging(self):
        credential = secret_digest('fixture-old')
        message = {'agent': 'researcher', 'generation': 'first'}
        entry = {'hiring_provider': {'account': ACCOUNT}, 'placement': 'remote', 'server': 'old.fixture.test', 'active': True,
                 'mobility_generation': 'first', 'mobility_rpc': {'node': 'old-node', 'digest': credential}}
        registry = {'agents': {'researcher': entry}}
        self.assertEqual(authorize_inference(registry, 'old-node', credential, message), 'researcher')
        move = {'agent': 'researcher', 'source': 'old.fixture.test', 'destination': 'new.fixture.test',
                'target': {'generation': 'next', 'node': 'new-node', 'rpc_token': 'fixture-new'}, 'phase': 'restoring'}
        registry['relocations'] = {'move': move}
        with self.assertRaises(PermissionError):
            authorize_inference(registry, 'new-node', secret_digest('fixture-new'), {**message, 'generation': 'next'})
        entry.update(mobility_move='move', mobility_fenced=True)
        with self.assertRaises(PermissionError):
            authorize_inference(registry, 'old-node', credential, message)
        self.assertEqual(authorize_inference(registry, 'new-node', secret_digest('fixture-new'),
                                             {**message, 'generation': 'next'}), 'researcher')
        entry['retirement'] = 'draining'
        with self.assertRaises(PermissionError):
            authorize_inference(registry, 'new-node', secret_digest('fixture-new'), {**message, 'generation': 'next'})
