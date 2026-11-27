import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from auto_tune import policy, propose
from deployment_snapshot import publish
from operation_timing import record
from operator_runtime import main as operator_main
from shared_provider import configure_team
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
                patch('shared_provider.choose_option', return_value='all'), patch('shared_provider.bind') as bind:
            data = Path(directory)
            configure_team(data, ['task-manager', 'helper', 'telegram-agent'])
            configure_team(data, ['helper'])
        catalog.assert_called_once()
        self.assertEqual([call.args[1] for call in bind.call_args_list], ['task-manager', 'helper', 'telegram-agent', 'helper'])
        self.assertTrue(all(call.args[2] == profile for call in bind.call_args_list))

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
