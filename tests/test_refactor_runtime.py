import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from agent_picker import BACK
from auto_tune import policy, propose
from deployment_snapshot import publish
from operation_timing import record
from operator_runtime import main as operator_main
from shared_provider import catalog, configure_team
from twin import validate


class RefactorTest(unittest.TestCase):
    def test_operator_runtime_mounts_the_home_telegram_file_read_only_for_setup(self):
        with TemporaryDirectory() as directory:
            home = Path(directory)
            file = home / 'telegram-api.json'
            secret = 'a' * 32
            file.write_text(json.dumps({'api_id': 1234, 'api_hash': secret}))
            file.chmod(0o600)
            data = home / 'state' / 'workspace'
            for command, expected in ((['install'], True), (['setup-guide'], True),
                                      (['telegram', 'setup'], True), (['setup-guide', '--print'], False),
                                      (['telegram', 'setup', '--api-file', str(home / 'other.json')], False),
                                      (['status'], False)):
                with self.subTest(command=command), patch('operator_runtime.preflight'), \
                        patch('operator_runtime.Path.home', return_value=home), \
                        patch('operator_runtime.subprocess.run') as run, \
                        patch('operator_runtime.os.execvp') as launch, \
                        patch.object(sys, 'argv', ['constellation_matrix', '--data-dir', str(data), *command]):
                    run.return_value.returncode = 0
                    operator_main()
                argv = launch.call_args.args[1]
                self.assertEqual('CONSTELLATION_TELEGRAM_API_FILE=' + str(file) in argv, expected)
                self.assertEqual(f'type=bind,source={file},target={file},readonly' in argv, expected)
                self.assertNotIn(secret, ' '.join(argv))

    def test_default_provider_profile_is_reused_and_resume_does_not_prompt_again(self):
        profile = {'account': 'a' * 24, 'mode': 'chatgpt_codex', 'model': 'fixture-model', 'effort': 'low'}
        with TemporaryDirectory() as directory, patch('shared_provider.catalog', return_value=profile) as catalog, \
                patch('shared_provider.ensure_coordinator', return_value={'accounts': []}), \
                patch('shared_provider.choose_option', return_value='all'), patch('shared_provider.bind') as bind:
            data = Path(directory)
            configure_team(data, ['task-manager', 'helper', 'telegram-agent'])
            configure_team(data, ['helper'])
        catalog.assert_called_once()
        self.assertEqual([call.args[1] for call in bind.call_args_list], ['task-manager', 'helper', 'telegram-agent', 'helper'])
        self.assertTrue(all(call.args[2] == profile for call in bind.call_args_list))

    def test_profile_review_can_return_to_effort_model_and_account_without_reconnecting(self):
        account = {'id': 'a' * 24, 'label': 'Work account', 'ready': True, 'mode': 'chatgpt_plan'}
        models = {'models': [{'id': 'model-a', 'efforts': ['low']},
                             {'id': 'model-b', 'efforts': ['medium', 'high']}]}
        selections = [account['id'], BACK, account['id'], 'model-a', BACK, 'model-b',
                      'medium', BACK, 'high', 'use']
        with patch('shared_provider.ensure_coordinator', return_value={'accounts': [account]}), \
                patch('shared_provider.control', return_value=models) as provider, \
                patch('shared_provider.choose_option', side_effect=selections) as choose:
            profile = catalog(Path('/fixture'))
        self.assertEqual(profile, {'account': account['id'], 'mode': 'chatgpt_plan', 'model': 'model-b', 'effort': 'high'})
        self.assertEqual(provider.call_count, 2)
        self.assertTrue(all(call.args[1]['action'] == 'catalog' for call in provider.call_args_list))
        self.assertEqual(choose.call_args_list[-2].args[2], 'medium')
        self.assertIn('Reasoning effort · high', choose.call_args.kwargs['details'])

    def test_shared_profile_displays_exact_values_and_can_revisit_the_previous_agent(self):
        default = {'account': 'a' * 24, 'mode': 'chatgpt_codex', 'model': 'fixture-model', 'effort': 'medium'}
        override = {**default, 'model': 'other-model', 'effort': 'high'}
        account = {'id': default['account'], 'mode': default['mode'], 'label': 'Work account'}
        with TemporaryDirectory() as directory, patch('shared_provider.catalog', side_effect=[default, override]) as select, \
                patch('shared_provider.ensure_coordinator', return_value={'accounts': [account]}), \
                patch('shared_provider.choose_option', side_effect=['custom', 'reuse', BACK, 'change', 'reuse']) as choose, \
                patch('shared_provider.bind') as bind:
            configure_team(Path(directory), ['task-manager', 'helper'])
        self.assertEqual([call.args[2] for call in bind.call_args_list], [override, default])
        self.assertEqual(select.call_args.kwargs, {'preferred': default, 'back': True})
        for call in choose.call_args_list:
            self.assertEqual(call.kwargs['details'], ('Provider · ChatGPT Codex', 'Account · Work account',
                                                     'Model · fixture-model', 'Reasoning effort · medium'))

    def test_config_snapshot_does_not_copy_compose_credentials_or_commands(self):
        with TemporaryDirectory() as directory:
            data = Path(directory)
            settings = {'project': 'fixture', 'password': 'secret-fixture'}
            manifest = {'services': {'engine-test': {'image': 'fixture:1', 'environment': {'TOKEN': 'secret-fixture'},
                          'healthcheck': {'test': ['CMD', 'secret-fixture'], 'timeout': '4s'}}}}
            publish(data, settings, {'agents': {}}, manifest)
            path = data / 'architecture/snapshot.json'
            self.assertNotIn('secret-fixture', path.read_text())
            self.assertEqual(json.loads(path.read_text())['services']['engine-test']['checks'], {'timeout': '4s'})

    def test_auto_config_only_accepts_bounded_startup_and_memory_fields(self):
        limits = {'services': {'engine-example': {'memory_mib': 320}}, 'memory_budget_mib': 128}
        self.assertEqual(policy(limits), limits)
        with self.assertRaises(ValueError):
            policy({**limits, 'services': {'engine-example': {'network': 42}}})
        service, container = {}, {'HostConfig': {'Memory': 256 * 1024**2}, 'State': {'OOMKilled': True}}
        self.assertEqual(propose(service, container, limits['services']['engine-example'], 12), ('mem_limit', 268))
        self.assertIsNone(propose(service, container, limits['services']['engine-example'], 0))
        container['State'] = {'Status': 'exited'}
        self.assertIsNone(propose(service, container, limits['services']['engine-example'], 100))

    def test_twin_requires_unique_agents_and_a_bounded_duration(self):
        plan = {'account': 'a' * 24, 'model': 'fixture-model', 'effort': 'low', 'revision': 'b' * 64,
                'agents': ['task-manager', 'helper'], 'minutes': 120}
        validate(plan)
        for overwrite in ({'minutes': 1440}, {'agents': ['helper', 'helper']}, {'account': '../secrets'}):
            with self.assertRaises(ValueError):
                validate({**plan, **overwrite})

    def test_digital_twin_setup_uses_selected_model_and_schedules_instead_of_starting(self):
        from twin import dashboard_snapshot, setup
        from installer import save
        with TemporaryDirectory() as working:
            data = Path(working)
            (data / 'architecture').mkdir()
            save(data / 'settings.json', {'control_runtime': True})
            save(data / 'agents.json', {'agents': {'the-architect': {'active': True}}})
            save(data / 'architecture/snapshot.json', {'revision': '1' * 64,
                'agents': {'the-architect': {'name': 'The Architect'}, 'helper': {'name': 'Helper'}}})
            profile = {'account': 'f' * 24, 'mode': 'chatgpt_codex', 'model': 'fixture-model', 'effort': 'low'}
            with (patch('twin.enqueue') as enqueue, patch('setup_guide.interactive_terminal', return_value=True),
                  patch('agent_picker.choose_option', side_effect=['start', '60']),
                  patch('agent_picker.terminal_picker', return_value=['helper']),
                  patch('shared_provider.catalog', return_value=profile)):
                setup(data)
            _, identifier, plan = enqueue.call_args.args
            self.assertEqual(plan, {**profile, 'agents': ['helper'], 'minutes': 60, 'revision': '1' * 64})
            self.assertEqual(json.loads((data / 'twins/setup.json').read_text())['id'], identifier)
            job = data / 'twins' / identifier
            job.mkdir()
            save(job / 'job.json', {'id': identifier, 'status': 'queued', 'created': 1, 'plan': plan,
                                    'grants': ['private-grant']})
            snapshot = dashboard_snapshot(data, 2)
            self.assertEqual(snapshot['jobs'][0]['agents'], ['helper'])
            self.assertNotIn('private-grant', json.dumps(snapshot))
            self.assertNotIn(profile['account'], json.dumps(snapshot))

    def test_timing_event_has_one_stable_replay_key_and_separate_queue_time(self):
        with TemporaryDirectory() as directory:
            outbox = Path(directory)
            record(outbox, 'provider-inference', time.monotonic_ns() - 6_000_000, 'completed', actor='helper', queue_ms=2)
            (path,) = outbox.glob('*.json')
            payload = json.loads(path.read_text())
            self.assertEqual(payload['idempotency_key'], 'timing-' + path.stem)
            self.assertEqual(payload['data']['timing']['queue_ms'], 2)
            self.assertGreaterEqual(payload['data']['timing']['work_ms'], 4)
            self.assertNotIn('prompt', payload['data'])
