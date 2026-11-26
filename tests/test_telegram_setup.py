from contextlib import redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
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
                data, ['engine', 'provider', 'credentials', 'bridge', 'login', 'start', 'ready', ''],
                inputs=[''], args=['--api-file', str(path)])
        self.assertEqual(actions, [call(data, ['prepare-agent']), call(data, ['sign-in']), call(data, ['probe']),
                                  call(data, ['prepare', str(path)]), call(data, ['activate']),
                                  call(data, ['start-agent'])])
        self.assertEqual(shell[-1], call(data, 'metrics'))
        self.assertEqual(prompts, 1)
        self.assertIn('!tg <your Telegram two-step verification password>', output)
        self.assertIn('Telegram Agent is running', output)

    def test_qr_can_pause_and_requires_confirmed_login_to_continue(self):
        data = Path('/srv/isolated-fixture')
        for stages, answers in ((['login'], [KeyboardInterrupt()]), (['login', 'login'], [''])):
            with self.subTest(stages=stages):
                output, actions, _, _ = self.run_setup(data, stages, inputs=answers)
                self.assertEqual(actions, [])
                self.assertIn('telegram setup', output)

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
            data, ['bridge', 'login', 'ready', ''], inputs=[''], install_agent=False)
        self.assertEqual(actions, [call(data, ['activate'])])
        self.assertTrue(all(item.args[2] == 'bridge' for item in shell[:-1]))
        self.assertEqual(prompts, 1)
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
