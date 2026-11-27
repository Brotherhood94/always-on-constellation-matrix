from contextlib import redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
import json
from pathlib import Path
from subprocess import CalledProcessError
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from telegram_setup import setup


class TelegramSetupTest(unittest.TestCase):
    def run_setup(self, data, stages, *, inputs=(), args=(), failure=None, install_agent=True):
        stages = [SimpleNamespace(stdout=stage + '\n') for stage in stages]
        with redirect_stdout(StringIO()) as output, patch('telegram_setup.interactive_terminal', return_value=True), \
                patch('telegram_setup.telegram', side_effect=failure) as telegram, \
                patch('telegram_setup.run_telegram', side_effect=stages) as shell, \
                patch('builtins.input', side_effect=inputs) as prompt:
            setup(data, args, install_agent=install_agent)
        return output.getvalue(), telegram.call_args_list, shell.call_args_list, prompt.call_count

    def test_fresh_setup_configures_provider_before_linking_the_operator(self):
        with TemporaryDirectory() as work:
            data = Path(work)
            path = data / 'telegram-api.json'
            path.touch(mode=0o600)
            output, actions, shell, prompts = self.run_setup(
                data, ['engine', 'provider', 'credentials', 'bridge', 'login', '', 'start', 'ready', ''],
                args=['--api-file', str(path)])
        self.assertEqual(actions, [call(data, ['prepare-agent']), call(data, ['sign-in']), call(data, ['probe']),
                                  call(data, ['prepare', str(path)]), call(data, ['activate']),
                                  call(data, ['start-agent'])])
        self.assertEqual(shell[-1], call(data, 'metrics'))
        self.assertEqual(prompts, 0)
        self.assertIn(call(data, 'link-account'), shell)
        self.assertNotIn('!tg', output)
        self.assertIn('Telegram Agent is running', output)

    def test_missing_api_file_opens_a_private_prompt_and_removes_the_temporary_file(self):
        with TemporaryDirectory() as work:
            data = Path(work)
            digest = 'a' * 32
            prepared = []

            def consume(workspace, arguments):
                self.assertEqual(workspace, data)
                self.assertEqual(arguments[0], 'prepare')
                path = Path(arguments[1])
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(path.read_text()), {'api_id': 1234, 'api_hash': digest})
                prepared.append(path)

            stages = [SimpleNamespace(stdout=stage) for stage in ('credentials', 'ready', '')]
            with redirect_stdout(StringIO()) as output, patch('telegram_setup.interactive_terminal', return_value=True), \
                    patch('telegram_setup.Path.home', return_value=data), patch.dict('os.environ', {'SUDO_USER': ''}), \
                    patch('telegram_setup.run_telegram', side_effect=stages), patch('telegram_setup.telegram', side_effect=consume), \
                    patch('builtins.input', side_effect=['', '1234']) as prompt, \
                    patch('telegram_setup.getpass.getpass', return_value=digest):
                setup(data)
            self.assertEqual(len(prepared), 1)
            self.assertFalse(prepared[0].exists())
            self.assertNotIn(digest, output.getvalue())
            self.assertIn('~/telegram-api.json', output.getvalue())
            self.assertIn('Telegram API JSON path', prompt.call_args_list[0].args[0])
            instructions = output.getvalue()
            for text in ('1. In your browser, open https://my.telegram.org/apps',
                         '\n\n2. Open API development tools', '\n\n3. In a second VPS terminal',
                         'chmod 600 ~/telegram-api.json', '"api_id": 123456',
                         '\n\n4. At the Telegram API JSON path prompt'):
                self.assertIn(text, instructions)

    def test_setup_accepts_the_mounted_operator_file_as_the_default(self):
        with TemporaryDirectory() as work:
            data = Path(work)
            file = data / 'telegram-api.json'
            file.touch(mode=0o600)
            with patch.dict('os.environ', {'CONSTELLATION_RUNTIME': 'compose',
                                           'CONSTELLATION_TELEGRAM_API_FILE': str(file)}):
                output, actions, _, prompts = self.run_setup(data, ['credentials', 'ready', ''], inputs=[''])
            self.assertEqual(actions, [call(data, ['prepare', str(file)])])
            self.assertEqual(prompts, 1)
            self.assertIn('~/telegram-api.json', output)

    def test_setup_accepts_a_custom_vps_path_and_retries_an_unreadable_path(self):
        with TemporaryDirectory() as work:
            data = Path(work)
            home = '/home/fixture'
            secret = 'b' * 32
            content = json.dumps({'api_id': 1234, 'api_hash': secret}).encode()
            stages = [SimpleNamespace(stdout=stage) for stage in ('credentials', 'ready', '')]
            imported = []

            def prepare(workspace, args):
                self.assertEqual(workspace, data)
                self.assertEqual(args[0], 'prepare')
                file = Path(args[1])
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)
                self.assertEqual(file.read_bytes(), content)
                imported.append(file)

            with redirect_stdout(StringIO()) as output, patch('telegram_setup.interactive_terminal', return_value=True), \
                    patch.dict('os.environ', {'CONSTELLATION_RUNTIME': 'compose', 'CONSTELLATION_OPERATOR_HOME': home,
                                              'CONSTELLATION_TELEGRAM_API_FILE': ''}), \
                    patch('operator_runtime.read_operator_credentials', side_effect=[ValueError('Unreadable file'), content]) as read, \
                    patch('telegram_setup.run_telegram', side_effect=stages), patch('telegram_setup.telegram', side_effect=prepare), \
                    patch('builtins.input', side_effect=['/missing.json', '~/telegram credentials.json']):
                setup(data)
            self.assertEqual(read.call_args_list, [call(Path('/missing.json')), call(Path(home) / 'telegram credentials.json')])
            self.assertEqual(len(imported), 1)
            self.assertFalse(imported[0].exists())
            self.assertIn('Unreadable file', output.getvalue())
            self.assertNotIn(secret, output.getvalue())

    def test_qr_can_pause_and_resume_before_confirmed_login(self):
        data = Path('/srv/isolated-fixture')
        stages = [SimpleNamespace(stdout='login'), CalledProcessError(130, ['link-account'])]
        with redirect_stdout(StringIO()) as output, patch('telegram_setup.interactive_terminal', return_value=True), \
                patch('telegram_setup.run_telegram', side_effect=stages) as shell:
            setup(data)
        self.assertEqual(shell.call_args_list[-1], call(data, 'link-account'))
        self.assertIn('Resume Telegram setup', output.getvalue())

    def test_resume_after_sign_in_failure_reuses_prepared_engine(self):
        data = Path('/srv/isolated-fixture')
        with self.assertRaises(CalledProcessError):
            self.run_setup(data, ['provider'], failure=CalledProcessError(1, ['sign-in']))
        _, actions, _, prompts = self.run_setup(data, ['provider', 'start', 'ready', ''])
        self.assertEqual(actions, [call(data, ['sign-in']), call(data, ['probe']), call(data, ['start-agent'])])
        self.assertEqual(prompts, 0)

    def test_authentication_rejection_offers_fresh_login_and_retries_the_probe(self):
        data = Path('/srv/isolated-fixture')
        failure = CalledProcessError(42, ['probe'])
        _, actions, _, prompts = self.run_setup(data, ['provider', 'start', 'ready', ''], inputs=[''],
                                                failure=[None, failure, None, None, None])
        self.assertEqual(actions, [call(data, ['sign-in']), call(data, ['probe']),
                                  call(data, ['sign-in', '--reauthenticate']), call(data, ['probe']),
                                  call(data, ['start-agent'])])
        self.assertEqual(prompts, 1)

    def test_bridge_only_setup_completes_without_configuring_amplifier(self):
        data = Path('/srv/isolated-fixture')
        output, actions, shell, prompts = self.run_setup(
            data, ['bridge', 'login', '', 'ready', ''], install_agent=False)
        self.assertEqual(actions, [call(data, ['activate'])])
        self.assertTrue(all(item.args[2] == 'bridge' for item in shell if item.args[1] == 'setup-state'))
        self.assertIn(call(data, 'link-account'), shell)
        self.assertEqual(prompts, 0)
        self.assertIn('Your Telegram account is linked', output)

    def test_ready_and_relocated_agents_require_no_setup(self):
        for stage in ('ready', 'managed'):
            with self.subTest(stage=stage):
                stages = [stage, ''] if stage == 'ready' else [stage]
                _, actions, _, prompts = self.run_setup(Path('/srv/isolated-fixture'), stages)
                self.assertEqual(actions, [])
                self.assertEqual(prompts, 0)


class TelegramProviderLoginTest(unittest.IsolatedAsyncioTestCase):
    async def test_forced_login_bypasses_locally_valid_credentials(self):
        provider = Mock(auth_status=Mock(return_value='authenticated'), login=AsyncMock(), close=AsyncMock())
        provider_type = Mock(return_value=provider)
        source = Path(__file__).resolve().parents[1] / 'integrations/telegram/engine/sign_in.py'
        spec = spec_from_file_location('telegram_sign_in', source)
        module = module_from_spec(spec)
        with patch.dict(sys.modules, {'amplifier_module_provider_openai_chatgpt':
                                      SimpleNamespace(ChatGPTProvider=provider_type)}), \
                patch.dict('os.environ', {'TELEGRAM_CODEX_TOKEN_FILE': '/fixture/codex-oauth.json'}), \
                redirect_stdout(StringIO()):
            spec.loader.exec_module(module)
            await module.sign_in()
            provider.login.assert_not_awaited()
            await module.sign_in(reauthenticate=True)
            provider.login.assert_awaited_once()
            self.assertEqual(provider.close.await_count, 2)


class TelegramAccountLinkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).resolve().parents[1] / 'integrations/telegram/link_account.py'
        spec = spec_from_file_location('telegram_account_link', source)
        cls.link = module_from_spec(spec)
        spec.loader.exec_module(cls.link)

    def test_qr_and_masked_password_follow_the_same_private_process(self):
        qr = {'login_id': 'fixture-process', 'type': 'display_and_wait', 'step_id': 'telegram.qr',
              'txn_id': 'qr-1', 'display_and_wait': {'type': 'qr', 'data': 'tg://login?token=fixture'}}
        password = {'type': 'user_input', 'step_id': 'telegram.password', 'txn_id': 'password-1',
                    'user_input': {'fields': [{'type': 'password', 'id': 'password'}]}}
        wrong = {**password, 'step_id': 'fi.mau.telegram.login.password.incorrect', 'txn_id': 'password-2'}
        request = Mock(side_effect=[qr, password, wrong, {'type': 'complete'}])
        show_qr = Mock()
        with redirect_stdout(StringIO()) as output, patch.object(self.link.getpass, 'getpass',
                                                                  side_effect=['fixture-wrong', 'fixture-secret']) as prompt:
            self.link.link_account(request, show_qr)
        show_qr.assert_called_once_with(qr['display_and_wait']['data'])
        self.assertEqual(prompt.call_count, 2)
        self.assertEqual(request.call_args_list, [
            call('start/qr'), call('step/fixture-process/telegram.qr/display_and_wait', payload=None, transaction='qr-1'),
            call('step/fixture-process/telegram.password/user_input', payload={'password': 'fixture-wrong'}, transaction='password-1'),
            call('step/fixture-process/fi.mau.telegram.login.password.incorrect/user_input',
                 payload={'password': 'fixture-secret'}, transaction='password-2'),
        ])
        self.assertIn('Your Telegram account is linked', output.getvalue())
        self.assertIn('Telegram rejected that password', output.getvalue())
        self.assertNotIn('fixture-secret', output.getvalue())

    def test_password_is_kept_out_of_process_arguments_and_echo_fallback_is_rejected(self):
        password = 'fixture "quoted" \u00e8'
        with patch.object(self.link.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{}')) as run:
            self.link.bridge_request(('compose', 'exec', '-T', 'telegram-bridge'), '@operator:matrix.test',
                                     'fixture-key', 'step/process/password/user_input', payload={'password': password})
        command, = run.call_args.args
        self.assertNotIn(password, ' '.join(command))
        self.assertNotIn('fixture-key', ' '.join(command))
        body = run.call_args.kwargs['input'].split('data-binary = ', 1)[1]
        self.assertEqual(json.loads(json.loads(body)), {'password': password})
        with patch.object(self.link.getpass, 'getpass', side_effect=self.link.getpass.GetPassWarning):
            with self.assertRaisesRegex(ValueError, 'hidden password entry'):
                self.link.masked_password()
