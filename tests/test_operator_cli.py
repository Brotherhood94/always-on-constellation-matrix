from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from installer import CLIENT, load, main, save
from operator_cli import (execute_host, install_as_operator, publish_metadata, setup_as_operator, telegram_as_operator)


class OperatorCLITest(unittest.TestCase):
    settings = {'server_name': 'fixture.test', 'owner': 'operator', 'tailscale': True, 'dot': False,
                'telegram': True, 'agents': ['manager'], 'matrix_port': 18500}

    def cli(self, data, *args):
        output = StringIO()
        with patch('sys.argv', ['constellation_matrix', '--data-dir', str(data), *args]), \
                patch('installer.os.geteuid', return_value=1000), redirect_stdout(output):
            main()
        return output.getvalue()

    def test_read_only_commands_use_sanitized_snapshots_without_elevating(self):
        with TemporaryDirectory() as work, patch('operator_cli.elevate') as sudo:
            data = Path(work)
            (data / 'dashboard').mkdir()
            publish_metadata(data, {**self.settings, 'owner_password_file': 'sensitive-path',
                                    'context_token': 'sensitive-fixture-token'}, {'agents': {}})
            info = data / 'setup.json'
            self.assertEqual(info.stat().st_mode & 0o777, 0o644)
            self.assertNotIn('sensitive', info.read_text())
            guide = self.cli(data, 'setup-guide', '--print')
            self.assertIn('Username: operator', guide)
            self.assertIn('Telegram → Telegram Agent', guide)
            self.assertNotIn('sudo ', guide)
            save(data / 'dashboard/integrations.json', {'time': 100, 'services': {'Telegram guide': 'running', 'Dot': 'ready'}})
            self.assertIn('Telegram guide  running', self.cli(data, 'telegram', 'status'))
            save(data / 'dashboard/telegram.json', {'generatedAt': '2026-01-01T00:00:00Z',
                'services': {'telegram-bridge': {'state': 'running', 'health': 'healthy'}},
                'pendingDrafts': 2, 'unconfirmedDeliveries': 1, 'paused': True})
            report = self.cli(data, 'telegram', 'status')
            self.assertIn('telegram-bridge  healthy', report)
            self.assertIn('Pending drafts  2', report)
            self.assertIn('Draft delivery paused', report)
            sudo.assert_not_called()

    def test_install_prompts_before_sudo_and_sends_passwords_only_on_stdin(self):
        output = StringIO()
        with TemporaryDirectory() as work, redirect_stdout(output), patch('operator_cli.run') as run, \
                patch('operator_cli.interactive_terminal', return_value=True), \
                patch('operator_cli.interactive', return_value=(self.settings, 'matrix-fixture-password')) as prompt, \
                patch('operator_cli.prompt_graph_password', return_value='graph-fixture-password') as graph, \
                patch('operator_cli.setup_as_operator') as guide:
            data = Path(work)
            order = Mock()
            for name, mock in (('prompt', prompt), ('graph', graph), ('sudo', run), ('guide', guide)):
                order.attach_mock(mock, name)

            def check_notice(*args, **kwargs):
                notice = output.getvalue()
                self.assertIn('Linux password for the VPS account shown in the prompt', notice)
                self.assertIn('start Docker services, write protected deployment files', notice)
                self.assertIn('enable startup at boot', notice)
                self.assertNotIn('fixture-password', notice)

            run.side_effect = check_notice
            install_as_operator(data, SimpleNamespace(config=None))
            self.assertEqual([item[0] for item in order.mock_calls], ['prompt', 'graph', 'sudo', 'guide'])
            self.assertEqual(run.call_args.args[-2:], ('_host', 'install'))
            self.assertNotIn('matrix-fixture-password', ' '.join(run.call_args.args))
            self.assertEqual(json.loads(run.call_args.kwargs['input'])['graph_password'], 'graph-fixture-password')
            self.assertEqual(json.loads(run.call_args.kwargs['input'])['password'], 'matrix-fixture-password')
            guide.assert_called_once_with(data, passphrase='matrix-fixture-password')

    def test_element_and_agent_selection_use_the_operator_session_before_elevation(self):
        output = StringIO()
        with TemporaryDirectory() as work, redirect_stdout(output), patch('operator_cli.confirm_element', return_value='next'), \
                patch('operator_cli.select_agents', return_value=['manager']), \
                patch('operator_cli.request') as request, patch('operator_cli.elevate') as sudo:
            data = Path(work)
            publish_metadata(data, self.settings, {'agents': {}})
            initial = {**load(data / 'setup.json'), 'select_agents': True}
            save(data / 'setup.json', initial)
            request.return_value = {'user_id': '@operator:fixture.test', 'access_token': 'matrix-session-fixture'}

            def mutate(_, __, action, *rest, payload=None):
                if action == 'enroll':
                    notice = output.getvalue()
                    self.assertIn('Authorize agent setup', notice)
                    self.assertIn('save agent registrations in the protected deployment directory', notice)
                    self.assertIn('start local Amplifier containers with Docker', notice)
                    self.assertIn('Linux password for the VPS account shown in the prompt', notice)
                    self.assertNotIn('matrix-fixture-password', notice)
                    self.assertEqual(payload, {'selection': ['manager'], 'token': 'matrix-session-fixture'})
                    save(data / 'setup.json', {**initial, 'element_confirmed': True, 'select_agents': False,
                                              'pending': ['manager']})
                if action == 'configure':
                    save(data / 'setup.json', {**load(data / 'setup.json'), 'pending': []})

            sudo.side_effect = mutate
            setup_as_operator(data, passphrase='matrix-fixture-password')
            self.assertEqual([item.args[2:] for item in sudo.call_args_list],
                             [('enroll',), ('configure', 'manager', '--pending-only'), ('finish',)])
            self.assertEqual(request.call_args_list[0].args[:2], ('POST', CLIENT + '/login'))
            self.assertEqual(request.call_args_list[-1],
                             call('POST', CLIENT + '/logout', {}, 'matrix-session-fixture', local_port=18500))

    def test_empty_selection_explains_why_saving_setup_progress_needs_sudo(self):
        output = StringIO()
        with TemporaryDirectory() as work, redirect_stdout(output), \
                patch('operator_cli.select_agents', return_value=[]), patch('operator_cli.request') as request, \
                patch('operator_cli.elevate') as sudo, patch('operator_cli.configure_as_operator'):
            data = Path(work)
            publish_metadata(data, {**self.settings, 'agents': []}, {'agents': {}})
            current = {**load(data / 'setup.json'), 'element_confirmed': True, 'select_agents': True}
            save(data / 'setup.json', current)

            def check_notice(*args, **kwargs):
                notice = output.getvalue()
                self.assertIn('save your setup progress in the protected deployment directory', notice)
                self.assertNotIn('start local Amplifier containers', notice)

            sudo.side_effect = check_notice
            setup_as_operator(data)
            sudo.assert_called_once_with(data, '_host', 'enroll', payload={'selection': []})
            request.assert_not_called()

    def test_telegram_passes_the_session_token_to_a_fixed_host_operation(self):
        with TemporaryDirectory() as work, patch('operator_cli.request') as request, \
                patch('operator_cli.elevate') as sudo, patch('operator_cli.password', return_value='matrix-fixture-password'):
            data = Path(work)
            publish_metadata(data, self.settings, {'agents': {}})
            request.return_value = {'user_id': '@operator:fixture.test', 'access_token': 'matrix-session-fixture'}
            telegram_as_operator(data, ['layout', '--bridge-room=!verified:fixture.test'])
            sudo.assert_called_once_with(data, '_host', 'telegram-layout', payload={
                'action': 'layout', 'token': 'matrix-session-fixture', 'bridge_room': '!verified:fixture.test'})
            self.assertEqual(request.call_args.args[1], CLIENT + '/logout')

    def test_internal_host_actions_require_sudo_and_validate_telegram_operation(self):
        with TemporaryDirectory() as work, redirect_stderr(StringIO()), patch('operator_cli.execute_host') as host:
            with self.assertRaises(SystemExit):
                self.cli(Path(work), '_host', 'metadata')
            host.assert_not_called()
        with TemporaryDirectory() as work, patch('sys.stdin', StringIO(json.dumps({
                'action': 'deactivate', 'token': 'fixture', 'bridge_room': None}))), patch('operator_cli.telegram') as telegram:
            with self.assertRaises(ValueError):
                execute_host(Path(work), SimpleNamespace(action='telegram-layout'))
            telegram.assert_not_called()
