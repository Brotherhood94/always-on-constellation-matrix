"""Installer and topology checks using deployment-independent fixtures."""
from contextlib import redirect_stderr, redirect_stdout
import curses
from io import StringIO
import json
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import DEFAULT, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy'))
from agent_picker import agent_checklist, choose_option, menu, select_agents, select_integrations, text_page
from installer import (
    PRESETS, agent_activate, agent_sign_in, close_office_setup_notice, context_config, install, interactive, load, main,
    normalized, operator_lock, password, prepare_graph_password, prepare_storage, provision_layout, register_entry, save,
    show_element_setup, tailscale_dns, telegram,
)
from agent_catalog import document
from compose import definition
from dot import connect as connect_dot
from onboarding import finish, guide, verified_pins
from remote import compose_remote
from setup_guide import confirm_element


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.presets = load(PRESETS)
        self.raw = {
            'server_name': 'fixture.test', 'owner': 'operator', 'agents': ['manager', 'coder', 'vps-manager', 'chief-of-staff'],
            'subscriptions': {'default': 'Work', 'overrides': {'coder': 'Personal'}}, 'dot': True, 'telegram': True,
        }
        self.settings = normalized(self.raw, self.presets)
        self.registry = {'agents': {}, 'dynamic_handoffs': True, 'local_server': 'fixture.test'}
        for key in self.settings['agents']:
            register_entry(self.settings, self.registry, key, self.presets[key])

    def test_agent_checklist_shows_profiles_and_toggles_agents(self):
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        screen.getch.side_effect = [
            ord(' '), curses.KEY_DOWN, ord('\n'), curses.KEY_DOWN, ord('\n'),
            curses.KEY_UP, ord('\n'), curses.KEY_DOWN, curses.KEY_DOWN, ord('\n'),
            *([curses.KEY_DOWN] * 4), ord('\n'),
        ]
        self.assertEqual(agent_checklist(screen, self.presets), ['reviewer', 'manager'])
        rendered = '\n'.join(call.args[2] for call in screen.addnstr.call_args_list)
        self.assertIn('🤖 Select agents to install', rendered)
        self.assertIn('[x] Coder', rendered)
        self.assertIn('Implement and test code changes.', rendered)
        self.assertIn('Role: coordinator', rendered)
        self.assertIn('Suggested model: gpt-6.2-astra  |  Reasoning: high', rendered)
        self.assertIn('>     Done', rendered)

    def test_agent_checklist_scrolls_and_can_confirm_an_empty_roster(self):
        screen = Mock()
        screen.getmaxyx.return_value = (20, 65)
        screen.getch.side_effect = [curses.KEY_DOWN] * 7 + [
            curses.KEY_UP, curses.KEY_UP, curses.KEY_ENTER, curses.KEY_ENTER,
            curses.KEY_DOWN, curses.KEY_DOWN, curses.KEY_ENTER,
        ]
        self.assertEqual(agent_checklist(screen, self.presets), [])
        rendered = '\n'.join(call.args[2] for call in screen.addnstr.call_args_list)
        self.assertIn('[x] Remote Worker', rendered)
        self.assertIn('Runs: remote', rendered)

    def test_integration_checklist_opens_with_all_selected_and_done_highlighted(self):
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        screen.getch.return_value = ord('\n')
        with patch('agent_picker.terminal_picker', side_effect=lambda render, *args, **kwargs:
                   render(screen, *args, **kwargs)):
            self.assertEqual(select_integrations(['telegram', 'dot']), ['element', 'telegram', 'dot'])
        rendered = [call.args[2] for call in screen.addnstr.call_args_list]
        self.assertIn('🔌 Choose workspace and integrations', rendered)
        self.assertIn('  [x] Element workspace (required)', rendered)
        self.assertIn('  [x] Telegram', rendered)
        self.assertIn('  [x] Dot', rendered)
        screen.addnstr.assert_any_call(7, 0, '>     Done', 79, curses.A_REVERSE)

    def test_integration_checklist_toggles_optional_items_and_keeps_element(self):
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        screen.getch.side_effect = [curses.KEY_UP, ord('\n')] * 3 + [curses.KEY_DOWN] * 3 + [ord('\n')]
        with patch('agent_picker.terminal_picker', side_effect=lambda render, *args, **kwargs:
                   render(screen, *args, **kwargs)):
            self.assertEqual(select_integrations(['telegram', 'dot']), ['element'])

    def test_integration_numbered_fallback_keeps_the_initial_selection(self):
        with patch('agent_picker.terminal_picker', return_value=None), \
             patch('builtins.input', return_value=''), redirect_stdout(StringIO()):
            self.assertEqual(select_integrations(['telegram']), ['element', 'telegram'])

    def test_agent_selection_can_use_line_input_without_a_terminal(self):
        output = StringIO()
        with patch('sys.stdin.isatty', return_value=False), patch('agent_picker.curses.wrapper') as picker, \
             patch('builtins.input', return_value=' coder, reviewer '), redirect_stdout(output):
            self.assertEqual(select_agents(self.presets), ['coder', 'reviewer'])
        picker.assert_not_called()
        self.assertIn('Analyze requirements, evidence and options', output.getvalue())

    def test_single_choice_menu_can_move_and_confirm(self):
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        screen.getch.side_effect = [curses.KEY_DOWN, curses.KEY_UP, curses.KEY_DOWN, ord('\n')]
        options = {'skip': 'Workspace only', 'dot': 'Enable Dot'}
        self.assertEqual(menu(screen, 'Choose integrations', options, 'skip'), 'dot')
        self.assertIn('> Enable Dot', [call.args[2] for call in screen.addnstr.call_args_list])

    def test_single_choice_fallback_accepts_a_number_or_default(self):
        options = {'first': 'First choice', 'second': 'Second choice'}
        with patch('agent_picker.terminal_picker', return_value=None), redirect_stdout(StringIO()), \
             patch('builtins.input', side_effect=['invalid', '2', '']):
            self.assertEqual(choose_option('Choose', options), 'second')
            self.assertEqual(choose_option('Choose', options, 'second'), 'second')

    def test_username_page_edits_and_retries_on_the_same_screen(self):
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        screen.get_wch.side_effect = [
            *'Fixturx', '\n', curses.KEY_HOME, curses.KEY_DC, 'f',
            curses.KEY_END, curses.KEY_BACKSPACE, 'e', '\n',
        ]
        validate = Mock(side_effect=['Use a valid username.', None])
        self.assertEqual(text_page(screen, 'Matrix operator username', validate, ''), 'fixture')
        self.assertEqual([call.args[0] for call in validate.call_args_list], ['Fixturx', 'fixture'])
        rendered = [call.args[2] for call in screen.addnstr.call_args_list]
        self.assertIn('Matrix operator username', rendered)
        self.assertIn('Use a valid username.', rendered)
        self.assertGreater(screen.erase.call_count, 1)

    def test_username_page_can_keep_the_existing_username(self):
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        screen.get_wch.return_value = '\n'
        validate = Mock(return_value=None)
        self.assertEqual(text_page(screen, 'Matrix operator username', validate, 'my-operator'), 'my-operator')
        validate.assert_called_once_with('my-operator')
        self.assertIn('Leave blank to keep my-operator.', [call.args[2] for call in screen.addnstr.call_args_list])

    def test_matrix_password_pages_mask_input_and_retry_a_mismatched_confirmation(self):
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        fixture = '  synthetic contraseña  '
        screen.get_wch.side_effect = [
            *'short', '\n', *([curses.KEY_BACKSPACE] * 5), *fixture, '\n',
            *'mismatch', '\n', *([curses.KEY_BACKSPACE] * 8), *fixture, '\n',
        ]
        with patch('agent_picker.terminal_picker', side_effect=lambda render, *args, **kwargs:
                   render(screen, *args, **kwargs)):
            self.assertEqual(password({}, confirm=True), fixture)
        rendered = '\n'.join(call.args[2] for call in screen.addnstr.call_args_list)
        self.assertIn('🔐 Matrix operator password', rendered)
        self.assertIn('🔑 Confirm Matrix operator password', rendered)
        self.assertIn('Save this password in a password manager before continuing.', rendered)
        self.assertIn('You need it to sign in to Element.', ' '.join(rendered.split()))
        self.assertIn('Use an operator password of at least 12 characters', rendered)
        self.assertIn('Enter the same password again', rendered)
        self.assertIn('*' * len(fixture), rendered)
        self.assertNotIn('short', rendered)
        self.assertNotIn('mismatch', rendered)
        self.assertNotIn('synthetic', rendered)

    def test_existing_matrix_password_screen_asks_for_element_password(self):
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        fixture = 'synthetic-password'
        screen.get_wch.side_effect = [*fixture, '\n']
        with patch('agent_picker.terminal_picker', side_effect=lambda render, *args, **kwargs:
                   render(screen, *args, **kwargs)):
            self.assertEqual(password({}), fixture)
        rendered = '\n'.join(call.args[2] for call in screen.addnstr.call_args_list)
        self.assertIn('🔐 Enter your existing Matrix operator password', rendered)
        self.assertIn('Use the same password you use to sign in to Element.', rendered)
        self.assertIn('*' * len(fixture), rendered)
        self.assertNotIn('Save this password', rendered)
        self.assertNotIn(fixture, rendered)

    def test_existing_matrix_password_fallback_uses_getpass(self):
        fixture = '  synthetic-password  '
        with patch('agent_picker.terminal_picker', return_value=None), \
             patch('agent_picker.getpass', return_value=fixture) as getpass, \
             patch('builtins.input') as line_input, redirect_stdout(StringIO()) as output:
            self.assertEqual(password({}), fixture)
        getpass.assert_called_once_with('🔐 Enter your existing Matrix operator password: ')
        line_input.assert_not_called()
        self.assertIn('Use the same password you use to sign in to Element.', output.getvalue())
        self.assertNotIn('Save this password', output.getvalue())

    def test_install_password_confirmation_also_uses_getpass_without_a_full_screen_terminal(self):
        fixture = 'synthetic-password'
        with patch('agent_picker.terminal_picker', return_value=None), \
             patch('agent_picker.getpass', side_effect=[fixture, 'wrong', fixture]) as getpass, \
             patch('builtins.input') as line_input, redirect_stdout(StringIO()) as output:
            self.assertEqual(password({}, confirm=True), fixture)
        self.assertEqual([call.args[0] for call in getpass.call_args_list], [
            '🔐 Matrix operator password: ', '🔑 Confirm Matrix operator password: ',
            '🔑 Confirm Matrix operator password: ',
        ])
        line_input.assert_not_called()
        self.assertNotIn(fixture, output.getvalue())

    def test_password_file_bypasses_the_screen_and_requires_private_permissions(self):
        with TemporaryDirectory() as work, patch('installer.prompt_text') as prompt:
            path = Path(work) / 'operator-password'
            fixture = '  synthetic-password  '
            path.write_text(fixture + '\n')
            path.chmod(0o600)
            self.assertEqual(password({'owner_password_file': str(path)}, confirm=True), fixture)
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'must be private'):
                password({'owner_password_file': str(path)})
        prompt.assert_not_called()

    def test_interactive_installer_requests_username_directly(self):
        passphrase = 'matrix-fixture-password'
        with patch('installer.choose_option', return_value='tailscale') as choose, \
             patch('installer.password', return_value=passphrase) as confirm, \
             patch('installer.select_integrations') as integrations, \
             patch('agent_picker.terminal_picker', return_value=None), \
             patch('installer.tailscale_dns', return_value='fixture.test'), \
             patch('builtins.input', return_value='my-operator') as answer:
            def select(_):
                confirm.assert_called_once_with({'owner': 'my-operator'}, confirm=True)
                return ['element', 'telegram', 'dot']

            integrations.side_effect = select
            raw, entered = interactive(self.presets, {})
            settings = normalized(raw, self.presets)
        self.assertEqual([call.args[0] for call in choose.call_args_list], ['🌐 Choose private access for Matrix and the dashboard'])
        integrations.assert_called_once_with(['telegram', 'dot'])
        answer.assert_called_once_with('👤 Matrix operator username: ')
        self.assertEqual(entered, passphrase)
        self.assertEqual(settings['agents'], [])
        self.assertNotIn('connection', settings)
        self.assertNotIn('subscriptions', settings)
        self.assertEqual(settings['owner'], 'my-operator')
        self.assertEqual(settings['server_name'], 'fixture.test')
        self.assertTrue(settings['tailscale'] and settings['dot'] and settings['telegram'])

    def test_interactive_installer_supports_custom_proxy_and_operator(self):
        with patch('installer.choose_option', return_value='proxy') as choose, \
             patch('installer.password'), \
             patch('installer.select_integrations', return_value=['element']), \
             patch('installer.tailscale_dns', return_value=None), \
             patch('agent_picker.terminal_picker', return_value=None), \
             patch('builtins.input', side_effect=['invalid', 'matrix.example', '', 'Coder', 'coder', 'my-operator']) as answer, \
             redirect_stdout(StringIO()):
            raw, _ = interactive(self.presets, {})
            settings = normalized(raw, self.presets)
        self.assertEqual(list(choose.call_args_list[0].args[1]), ['proxy'])
        self.assertEqual(answer.call_count, 6)
        self.assertEqual([call.args[0] for call in answer.call_args_list[:2]], ['🌐 Private Matrix DNS name: '] * 2)
        self.assertFalse(settings['tailscale'])
        self.assertEqual(settings['server_name'], 'matrix.example')
        self.assertEqual(settings['owner'], 'my-operator')

    def test_interactive_rerun_keeps_the_existing_operator_username(self):
        with patch('installer.choose_option', return_value='tailscale'), \
             patch('installer.password'), \
             patch('installer.select_integrations', return_value=['element', 'telegram']) as integrations, \
             patch('agent_picker.terminal_picker', return_value=None), \
             patch('installer.tailscale_dns', return_value='fixture.test'), \
             patch('builtins.input', side_effect=['different-operator', '']) as answer, redirect_stdout(StringIO()):
            saved = {'owner': 'my-operator', 'telegram': True, 'dot': False, 'agents': ['coder']}
            raw, _ = interactive(self.presets, saved)
            settings = normalized(raw, self.presets)
        self.assertEqual(settings['owner'], 'my-operator')
        self.assertEqual(settings['agents'], ['coder'])
        integrations.assert_called_once_with(['telegram'])
        self.assertTrue(settings['telegram'])
        self.assertFalse(settings['dot'])
        self.assertEqual([call.args[0] for call in answer.call_args_list], ['👤 Matrix operator username [my-operator]: '] * 2)

    def test_tailnet_suggestion_requires_a_running_https_node(self):
        status = {'BackendState': 'Running', 'Self': {'DNSName': 'fixture.test.'}, 'CertDomains': ['fixture.test']}
        result = CompletedProcess([], 0, json.dumps(status))
        with patch('installer.run', return_value=result):
            self.assertEqual(tailscale_dns(), 'fixture.test')
            result.stdout = json.dumps({**status, 'CertDomains': []})
            self.assertIsNone(tailscale_dns())
        with patch('installer.run', side_effect=FileNotFoundError):
            self.assertIsNone(tailscale_dns())

    def test_element_setup_prints_download_and_deployment_details(self):
        output = StringIO()
        with redirect_stdout(output):
            show_element_setup(self.settings)
        rendered = output.getvalue()
        self.assertEqual(rendered.lstrip().splitlines()[0], '✨ Finalize your Constellation setup')
        self.assertNotIn('\x1b[', rendered)
        self.assertIn('https://element.io/download', rendered)
        self.assertIn('Choose Sign in. Under Account provider', rendered)
        self.assertIn('Click Edit to the right of the server name, above Continue.', rendered)
        self.assertIn('Paste your Matrix homeserver into Other account provider and select Continue.', rendered)
        self.assertIn('https://fixture.test', [line.strip() for line in rendered.splitlines()])
        self.assertIn('Username: operator', rendered)
        self.assertIn('Your Matrix operator password from setup', rendered)
        self.assertIn('Agents → Office', rendered)
        self.assertIn('Tailscale', rendered)
        self.assertIn('Agents → VPS Alerts → Pinned messages', rendered)
        self.assertIn('Pinned messages → Workspace shortcuts', rendered)
        self.assertNotIn(self.settings['dashboard_url'], rendered)
        self.assertNotIn(self.settings['context_url'], rendered)
        self.assertNotIn('Keep the full setup checklist handy', rendered)
        self.assertIn('sudo ./constellation_matrix setup-guide', rendered)
        output = StringIO()
        with redirect_stdout(output):
            show_element_setup({**self.settings, 'tailscale': False})
        self.assertNotIn('Tailscale', output.getvalue())

    def test_setup_guide_uses_custom_paths_without_disclosing_stored_credentials(self):
        with TemporaryDirectory() as work:
            data = Path(work).resolve() / 'fixture workspace'
            secrets = {
                'context/neo4j-password': 'synthetic-graph-password',
                'dot/runtime-key': 'sk-synthetic-runtime-key',
                'telegram/bridge/config.yaml': 'synthetic-telegram-api-hash',
            }
            for name, value in secrets.items():
                (data / name).parent.mkdir(parents=True, exist_ok=True)
                save(data / name, value)
            save(data / 'settings.json', self.settings)
            command = ['constellation_matrix', '--data-dir', str(data), 'setup-guide']
            output = StringIO()
            with patch('installer.os.geteuid', return_value=0), patch('sys.argv', command), redirect_stdout(output):
                main()
            rendered = output.getvalue()
            lines = [line.strip() for line in rendered.splitlines()]
            for secret in secrets.values():
                self.assertNotIn(secret, rendered)
            invocation = f"./constellation_matrix --data-dir '{data}'"
            self.assertNotIn('dot-connect', rendered)
            self.assertNotIn('telegram prepare', rendered)
            self.assertNotIn('neo4j-password', rendered)
            self.assertIn(f'{invocation} setup-guide', lines)

    def test_setup_guide_shows_steps_for_selected_integrations(self):
        for dot, telegram in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(dot=dot, telegram=telegram):
                output = StringIO()
                with redirect_stdout(output):
                    show_element_setup({**self.settings, 'dot': dot, 'telegram': telegram})
                rendered = output.getvalue()
                self.assertEqual('Pinned messages → Connect Dot' in rendered, dot)
                self.assertEqual('Telegram → Rooms → Telegram Bridge → Pinned messages' in rendered, telegram)
                self.assertEqual(rendered.count('💬 Element Desktop'), 1)
                self.assertNotIn('🔌 Dot · Secure MCP Tunnel', rendered)
                self.assertNotIn('✈️ Telegram · Bridge and drafting agent', rendered)

    def test_setup_guide_uses_color_only_when_enabled(self):
        for environment, colored in [({'TERM': 'xterm'}, True), ({'TERM': 'dumb'}, False),
                                     ({'TERM': 'xterm', 'NO_COLOR': ''}, False)]:
            with self.subTest(environment=environment):
                output = StringIO()
                with patch.dict('os.environ', environment, clear=True), \
                        patch.object(output, 'isatty', return_value=True), redirect_stdout(output):
                    show_element_setup(self.settings)
                self.assertEqual('\x1b[1;36m' in output.getvalue(), colored)

    def test_element_page_handles_enter_right_arrow_and_pause(self):
        output = StringIO()
        with redirect_stdout(output), patch.dict('setup_guide.os.environ', {'TERM': 'xterm'}), \
                patch('sys.stdin.fileno', return_value=10), \
                patch('setup_guide.termios.tcgetattr'), patch('setup_guide.tty.setcbreak'), \
                patch('setup_guide.termios.tcsetattr') as restore, \
                patch('setup_guide.select', return_value=([10], [], [])), \
                patch('setup_guide.os.read', side_effect=[b'\n', b'\x1b', b'[', b'C', KeyboardInterrupt]):
            self.assertEqual(confirm_element('Element', 'Install Element.'), 'next')
            self.assertEqual(confirm_element('Element', 'Install Element.'), 'next')
            self.assertIsNone(confirm_element('Element', 'Install Element.'))
        self.assertEqual(restore.call_count, 3)
        self.assertIn('→ I’m signed in (Enter)', output.getvalue())

    def test_basic_terminal_continues_from_element_to_agent_setup(self):
        with patch.dict('setup_guide.os.environ', {'TERM': 'dumb'}), \
             patch('builtins.input', side_effect=['', EOFError]) as prompt, redirect_stdout(StringIO()) as output:
            self.assertEqual(confirm_element('Element', 'Install Element.'), 'next')
            self.assertIsNone(confirm_element('Element', 'Install Element.'))
        self.assertEqual(prompt.call_count, 2)
        self.assertIn('Amplifier will then open provider and model setup', output.getvalue())
        self.assertNotIn('\x1b[2J', output.getvalue())

    def test_wizard_resumes_element_and_immediately_opens_agent_selection(self):
        settings = {**self.settings, 'agents': []}
        with TemporaryDirectory() as work, redirect_stdout(StringIO()) as output:
            data = Path(work)
            registry = {'agents': {}}
            save(data / 'settings.json', settings)
            save(data / 'agents.json', registry)
            save(data / 'onboarding.json', {'confirmed': [], 'select_agents': True})
            command = 'sudo ./constellation_matrix'
            with patch('onboarding.confirm_element', return_value=None) as confirm, \
                 patch('onboarding.select_agents') as picker:
                guide(data, settings, command, None)
            picker.assert_not_called()
            self.assertEqual(confirm.call_args.args[0], '💬 Element Desktop')
            self.assertEqual(load(data / 'onboarding.json'), {'confirmed': [], 'select_agents': True})
            self.assertIn(f'{command} setup-guide', output.getvalue())

            def pick(_):
                self.assertEqual(load(data / 'onboarding.json')['confirmed'], ['element'])
                return []

            with patch('onboarding.confirm_element', return_value='next') as confirm, \
                 patch('onboarding.select_agents', side_effect=pick) as picker, \
                 patch('onboarding.open_existing', return_value=(settings, registry)), \
                 patch('onboarding.provision_selected_agents') as provision:
                guide(data, settings, command, None)
                guide(data, settings, command, None)
            confirm.assert_called_once()
            picker.assert_called_once()
            provision.assert_called_once_with(data, settings, registry, [], None)
            self.assertTrue(load(data / 'onboarding.json')['welcome']['skipped'])
            self.assertEqual((data / 'onboarding.json').stat().st_mode & 0o777, 0o600)

    def test_legacy_element_confirmation_and_welcome_receipt_survive(self):
        settings = {**self.settings, 'agents': []}
        with TemporaryDirectory() as work, redirect_stdout(StringIO()):
            data = Path(work)
            save(data / 'settings.json', settings)
            save(data / 'agents.json', {'agents': {}})
            state = data / 'onboarding.json'
            receipt = {'confirmed': ['element', 'context'], 'welcome': {'event': '$existing'}}
            save(state, receipt)
            with patch('onboarding.confirm_element') as confirm, \
                    patch('onboarding.deliver_welcome') as welcome:
                guide(data, settings, 'fixture', None)
            confirm.assert_not_called()
            self.assertEqual(load(state), receipt)
            welcome.assert_called_once_with(data, 'fixture')

    def test_explicit_print_bypasses_the_wizard_in_a_terminal(self):
        with TemporaryDirectory() as work, redirect_stdout(StringIO()) as output:
            data = Path(work).resolve()
            save(data / 'settings.json', self.settings)
            argv = ['constellation_matrix', '--data-dir', str(data), 'setup-guide', '--print']
            with patch('installer.os.geteuid', return_value=0), patch('sys.argv', argv), \
                 patch('setup_guide.interactive_terminal', return_value=True), patch('onboarding.guide') as wizard:
                main()
            wizard.assert_not_called()
            self.assertIn('💬 Element Desktop', output.getvalue())
            self.assertIn('👋 Welcome to Constellation', output.getvalue())
            self.assertFalse((data / 'onboarding.json').exists())

    def test_graph_password_is_confirmed_and_kept_across_reruns(self):
        graph_password = """Graph test #'$" \\!"""
        with TemporaryDirectory() as work:
            data = Path(work)
            def shown(title, validate, **kwargs):
                self.assertTrue(kwargs['masked'])
                self.assertIsNone(validate(graph_password))
                self.assertIsNotNone(validate('bad'))
                self.assertIsNotNone(validate('fixture/slash password'))
                self.assertIsNotNone(validate('-fixture password'))
                return graph_password

            with patch('installer.prompt_text', side_effect=shown) as prompt:
                prepare_graph_password(data, None, interactive=True)
                prepare_graph_password(data, None, interactive=True)
            self.assertEqual([call.args[0] for call in prompt.call_args_list], [
                '🧠 Context Intelligence password', '🔑 Confirm Context Intelligence password',
            ])
            saved = data / 'context/neo4j-password'
            self.assertEqual(saved.read_text(), graph_password + '\n')
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
            with patch('installer.os.chown'), patch('installer.os.fchown'):
                context_config({**self.settings, 'telegram': False}, data, {'agents': {}})
            self.assertEqual((data / 'context').stat().st_mode & 0o777, 0o700)
            self.assertEqual((data / 'context/neo4j-auth').read_text(), f'neo4j/{graph_password}\n')
            self.assertEqual(load(data / 'context/config/server.json')['neo4j_password'], graph_password)

    def test_configured_graph_password_requires_a_private_file_and_preserves_existing_auth(self):
        with TemporaryDirectory() as work:
            data = Path(work)
            supplied = data / 'private.txt'
            save(supplied, 'separate config password\n', mode=0o644)
            with self.assertRaisesRegex(ValueError, 'must be private'):
                prepare_graph_password(data, str(supplied), interactive=False)
            supplied.chmod(0o600)
            prepare_graph_password(data, str(supplied), interactive=False)
            save(supplied, 'different config password\n')
            with self.assertRaisesRegex(ValueError, 'existing Context Intelligence password'):
                prepare_graph_password(data, str(supplied), interactive=False)
            self.assertEqual((data / 'context/neo4j-password').read_text(), 'separate config password\n')

    def test_roster_selection_is_saved_before_provisioning_and_resumed_after_failure(self):
        settings = {**self.settings, 'agents': [], 'dot': False, 'telegram': False}
        registry = {'agents': {}}
        with TemporaryDirectory() as work, redirect_stdout(StringIO()):
            data = Path(work)
            save(data / 'settings.json', settings)
            state = data / 'onboarding.json'
            save(state, {'confirmed': ['element', 'dashboard', 'context'], 'select_agents': True})

            def enroll(directory, saved, previous, selection, passphrase):
                self.assertEqual(load(state)['selection'], selection)
                save(data / 'settings.json', {**saved, 'agents': selection})
                for key in selection:
                    register_entry(saved, previous, key, self.presets[key])
                save(data / 'agents.json', previous)

            with patch('onboarding.select_agents', return_value=['manager']) as pick, \
                 patch('onboarding.open_existing', return_value=(settings, registry)), \
                 patch('onboarding.provision_selected_agents', side_effect=[OSError('retry enrollment')]) as provision, \
                 patch('onboarding.agent_sign_in') as sign_in, patch('onboarding.deliver_welcome') as welcome:
                guide(data, settings, 'fixture', 'fixture-password')
                self.assertEqual(load(state)['selection'], ['manager'])
                sign_in.assert_not_called()
                welcome.assert_not_called()
                provision.side_effect = enroll
                guide(data, settings, 'fixture', 'fixture-password')
            pick.assert_called_once()
            sign_in.assert_called_once_with(data, selection=['manager'])
            welcome.assert_called_once_with(data, 'fixture')
            self.assertFalse(load(state).get('select_agents'))

    def test_provisioning_prepares_roster_management_in_the_existing_operator_session(self):
        settings = {**self.settings, 'dot': False, 'telegram': False}
        rooms = {'owner': '@operator:fixture.test', 'office': '!office', 'alerts': '!alerts'}
        with TemporaryDirectory() as work, patch('installer.login', side_effect=[
                {'user_id': rooms['owner'], 'access_token': 'operator-session'},
                {'access_token': 'monitor-session'}]), patch('installer.private_secret'), \
             patch('installer.create_layout', return_value=rooms), patch('installer.prepare_agent_state'), \
             patch('installer.os.fchown'), patch('installer.request') as matrix, \
             patch('roster.prepare_management') as prepare:
            data = Path(work)
            (data / 'bot').mkdir()

            def prepare_before_logout(*_):
                self.assertEqual(load(data / 'rooms.json'), rooms)
                self.assertNotIn('operator-session', [call.args[-1] for call in matrix.call_args_list])

            prepare.side_effect = prepare_before_logout
            provision_layout(settings, data, self.registry, 'synthetic-password')
            prepare.assert_called_once_with(data, settings, self.registry, 'operator-session')
            self.assertEqual(matrix.call_args.args[-1], 'operator-session')
            self.assertFalse((data / 'bot/office-setup.json').exists())

    def test_office_setup_notice_is_provisioned_once_for_a_pending_interactive_roster(self):
        settings = {**self.settings, 'dot': False, 'telegram': False}
        rooms = {'owner': '@operator:fixture.test', 'office': '!office', 'alerts': '!alerts'}
        with TemporaryDirectory() as work, patch('installer.login', return_value={
                'user_id': rooms['owner'], 'access_token': 'operator-session'}), patch('installer.private_secret'), \
                patch('installer.create_layout', return_value=rooms), patch('installer.invite') as invite, \
                patch('installer.os.fchown'), patch('installer.request'), patch('roster.prepare_management'):
            data = Path(work) / 'workspace path'
            (data / 'bot').mkdir(parents=True)
            path = data / 'bot/office-setup.json'
            for _ in range(2):
                provision_layout(settings, data, {'agents': {}}, 'synthetic-password', office_setup=True)
            invite.assert_called_once_with('!office', '@monitor:fixture.test', 'operator-session')
            self.assertEqual(load(path), {'room': '!office', 'pending': True,
                                          'command': f"./constellation_matrix --data-dir '{data}' setup-guide"})
            close_office_setup_notice(data)
            self.assertFalse(load(path)['pending'])

    def test_setup_resumes_management_before_the_welcome_without_repeating_provider_setup(self):
        settings = {**self.settings, 'agents': ['chief-of-staff'], 'dot': False, 'telegram': False}
        with TemporaryDirectory() as work, redirect_stdout(StringIO()) as output, \
             patch('onboarding.agent_sign_in') as sign_in, \
             patch('onboarding.enable_management', side_effect=[OSError('broker interrupted'), None]) as enable, \
             patch('onboarding.deliver_welcome') as welcome:
            data = Path(work)
            save(data / 'settings.json', settings)
            save(data / 'agents.json', {'agents': {'chief-of-staff': {'active': False, 'placement': 'local'}}})
            save(data / 'onboarding.json', {'confirmed': ['element', 'dashboard', 'context']})
            guide(data, settings, 'fixture', None)
            sign_in.assert_called_once_with(data, selection=['chief-of-staff'])
            enable.assert_called_once_with(data, automatic=True)
            welcome.assert_not_called()
            self.assertIn('Resume setup with fixture setup-guide', output.getvalue())
            save(data / 'agents.json', {'agents': {'chief-of-staff': {'active': True, 'placement': 'local'}}})
            guide(data, settings, 'fixture', None)
            self.assertEqual(sign_in.call_count, 1)
            self.assertEqual(enable.call_count, 2)
            welcome.assert_called_once_with(data, 'fixture')

    def test_welcome_waits_for_remote_readiness_membership_and_pins_then_keeps_its_sender(self):
        settings = {**self.settings, 'agents': ['coder', 'manager', 'remote-worker'], 'dot': False, 'telegram': False}
        register_entry(self.settings, self.registry, 'remote-worker', self.presets['remote-worker'])
        entries = self.registry['agents']
        with TemporaryDirectory() as work, patch('onboarding.open_existing', return_value=(settings, self.registry)), \
             patch('onboarding.session_token', return_value='fixture-token') as token, \
             patch('onboarding.publish', side_effect=[OSError('lost response'), '$welcome']) as publish, \
             patch('onboarding.verified_pins') as pins, patch('onboarding.request') as request, \
             patch('onboarding.read_password_file'), patch('onboarding.choice', return_value='coder') as pick:
            data = Path(work)
            state = data / 'onboarding.json'
            save(data / 'rooms.json', {'office': '!office', 'alerts': '!alerts', 'owner': '@operator:fixture.test'})
            self.assertIn('Finish Element sign-in and agent selection', finish(data)['waiting'])
            save(state, {'confirmed': ['element', 'dashboard', 'context']})
            entries['coder']['active'] = entries['manager']['active'] = True
            self.assertIn('Remote Worker', finish(data)['waiting'])
            token.assert_not_called()
            entries['remote-worker']['active'] = True
            members = {'@manager:fixture.test': {}, '@operator:fixture.test': {}}
            request.return_value = {'joined': members}
            self.assertIn('join Office', finish(data)['waiting'])
            members.update({'@coder:fixture.test': {}, '@remote-worker:fixture.test': {}})
            pins.return_value = None
            self.assertIn('Waiting for Open dashboard', finish(data)['waiting'])
            publish.assert_not_called()
            pins.return_value = {'dashboard': 'https://matrix.to/dashboard', 'context': 'https://matrix.to/context'}
            with self.assertRaisesRegex(OSError, 'lost response'):
                finish(data)
            attempt = load(state)['welcome_attempt']
            self.assertEqual(attempt['sender'], '@coder:fixture.test')
            self.assertEqual(attempt['room'], '!office')
            pick.reset_mock()
            receipt = {'sender': '@coder:fixture.test', 'room': '!office', 'event': '$welcome'}
            self.assertEqual(finish(data), receipt)
            self.assertEqual(finish(data), receipt)
            pick.assert_not_called()
            self.assertEqual(publish.call_args_list[0], publish.call_args_list[1])
            self.assertEqual(publish.call_args.args[-1]['name'], 'Manager')

    def test_welcome_prefers_chief_of_staff_and_checks_the_private_chat(self):
        settings = {**self.settings, 'agents': ['chief-of-staff', 'manager'], 'dot': False, 'telegram': False}
        for key in settings['agents']:
            self.registry['agents'][key]['active'] = True
        with TemporaryDirectory() as work, patch('onboarding.open_existing', return_value=(settings, self.registry)), \
             patch('onboarding.session_token'), patch('onboarding.read_password_file'), \
             patch('onboarding.publish', return_value='$welcome') as publish, patch('onboarding.verified_pins'), \
             patch('onboarding.request') as request, patch('onboarding.choice') as pick:
            data = Path(work)
            owner = '@operator:fixture.test'
            chief = '@chief-of-staff:fixture.test'
            save(data / 'rooms.json', {'office': '!office', 'alerts': '!alerts', 'owner': owner})
            save(data / 'onboarding.json', {'confirmed': ['element', 'dashboard', 'context']})
            matrix = data / 'state/chief-of-staff/matrix'
            matrix.mkdir(parents=True)
            save(matrix / 'room-id', '!chief')
            members = {'joined': {owner: {}, chief: {}, '@manager:fixture.test': {}}}
            rules = {'type': 'm.room.join_rules', 'content': {'join_rule': 'invite'}}
            dm = [rules, *({'type': 'm.room.member', 'state_key': user, 'content': {'membership': 'join'}}
                          for user in (owner, chief))]
            extra = {'type': 'm.room.member', 'state_key': '@other:fixture.test', 'content': {'membership': 'invite'}}
            request.side_effect = [members, [*dm, extra], members, dm]
            with self.assertRaisesRegex(ValueError, 'requires the operator and Chief of Staff'):
                finish(data)
            publish.assert_not_called()
            self.assertEqual(finish(data), {'sender': chief, 'room': '!chief', 'event': '$welcome'})
            pick.assert_not_called()

    def test_verifies_both_links_in_one_encrypted_monitor_pin(self):
        with TemporaryDirectory() as work, patch('onboarding.request') as request:
            data = Path(work)
            bot = data / 'bot'
            bot.mkdir()
            save(bot / 'token', 'fixture-monitor-token')
            save(bot / 'shortcut-pins.json', {'entries': {
                'dashboard': {'event': '$shortcuts', 'body': f"Open dashboard — {self.settings['dashboard_url']}"},
                'context': {'event': '$shortcuts', 'body': f"Open Context Intelligence — {self.settings['context_url']}"},
            }})
            pinned = {'pinned': ['$shortcuts', '$unrelated']}
            event = {'type': 'm.room.encrypted', 'sender': '@monitor:fixture.test'}
            request.side_effect = [{'pinned': ['$unrelated']}, pinned, event]
            self.assertIsNone(verified_pins(data, self.settings, '!alerts'))
            self.assertEqual(verified_pins(data, self.settings, '!alerts'), {
                'dashboard': 'https://matrix.to/#/%21alerts/%24shortcuts',
                'context': 'https://matrix.to/#/%21alerts/%24shortcuts',
            })

    def test_dot_startup_retries_quietly_and_explains_persistent_failure(self):
        with TemporaryDirectory() as work, patch('dot.Path.is_socket', return_value=True), \
             patch('dot.unit'), patch('dot.run') as run:
            data = Path(work).resolve()
            (data / 'dot').mkdir()
            (data / 'dot/tunnel-client').touch()
            save(data / 'dot/runtime-key', 'synthetic-key')
            tunnel = 'tunnel_' + 'a' * 32
            output = StringIO()
            with redirect_stdout(output):
                connect_dot(self.settings, data, tunnel)
            curl = run.call_args.args
            self.assertEqual(curl[0], 'curl')
            self.assertIn('--silent', curl)
            self.assertIn('--retry-all-errors', curl)
            self.assertNotIn('--show-error', curl)
            self.assertEqual(output.getvalue().splitlines(), [
                'Waiting for the Dot tunnel to connect…', 'Dot tunnel is ready.',
            ])

            run.side_effect = [None, None, None, CalledProcessError(7, curl)]
            output = StringIO()
            with redirect_stdout(output), self.assertRaisesRegex(ValueError, 'Dot tunnel did not become ready.*journalctl'):
                connect_dot(self.settings, data, tunnel)
            self.assertNotIn('Dot tunnel is ready.', output.getvalue())

    def test_last_local_agent_configuration_prints_element_setup(self):
        self.registry['agents']['chief-of-staff']['active'] = True
        self.registry['agents']['vps-manager']['active'] = True
        register_entry(self.settings, self.registry, 'remote-worker', self.presets['remote-worker'])
        with TemporaryDirectory() as work:
            data = Path(work).resolve()
            registry_path = data / 'agents.json'
            save(registry_path, self.registry)
            save(data / 'settings.json', self.settings)

            def activate(directory, key):
                self.assertEqual(data, directory)
                registry = load(registry_path)
                registry['agents'][key]['active'] = True
                save(registry_path, registry)

            command = ['constellation_matrix', '--data-dir', work, 'agent', 'configure']
            with patch('installer.os.geteuid', return_value=0), patch('installer.agent_sign_in', side_effect=activate):
                earlier = StringIO()
                with patch('sys.argv', [*command, 'manager']), redirect_stdout(earlier):
                    main()
                self.assertNotIn('Finalize your Constellation setup', earlier.getvalue())

                final = StringIO()
                with patch('sys.argv', [*command, 'coder']), redirect_stdout(final):
                    main()
                self.assertIn('Finalize your Constellation setup', final.getvalue())
                self.assertIn('Workspace shortcuts', final.getvalue())

    def test_legacy_connection_config_preserves_existing_agent_identity(self):
        raw = dict(self.raw)
        del raw['subscriptions']
        settings = normalized({**raw, 'connection': 'chatgpt-codex'}, self.presets)
        self.assertNotIn('connection', settings)
        register_entry(settings, self.registry, 'coder', self.presets['coder'])
        self.assertEqual(self.registry['agents']['coder']['subscription'], 'Personal')
        with self.assertRaises(ValueError):
            normalized({**raw, 'connection': 'unsupported'}, self.presets)

    def test_interactive_install_deploys_the_workspace_before_choosing_agents(self):
        config = {**self.raw, 'systemd': False, 'agents': []}
        config.pop('subscriptions')
        with TemporaryDirectory() as work, redirect_stdout(StringIO()) as output:
            data = Path(work) / 'fixture workspace'
            with patch.multiple(
                'installer', interactive=DEFAULT, password=DEFAULT, prepare_storage=DEFAULT,
                command_output=DEFAULT, compose_config=DEFAULT, compose=DEFAULT, service_units=DEFAULT,
                write_config=DEFAULT, up=DEFAULT, provision_layout=DEFAULT, prepare_images=DEFAULT,
                sync_workshop=DEFAULT, publish_inventory=DEFAULT, configure_network=DEFAULT,
                agent_sign_in=DEFAULT, choose_option=DEFAULT, finalize_setup=DEFAULT, prepare_graph_password=DEFAULT, run_telegram=DEFAULT,
            ) as steps, patch('setup_guide.interactive_terminal', return_value=True):
                steps['interactive'].return_value = (config, 'matrix-fixture-password')
                steps['command_output'].return_value = '[]'

                def start_services(path, *services):
                    if 'monitor' in services:
                        steps['compose'].assert_any_call(path, 'build', 'monitor')

                def open_wizard(path, *, passphrase):
                    self.assertEqual(path, data)
                    self.assertEqual(passphrase, 'matrix-fixture-password')
                    steps['agent_sign_in'].assert_not_called()

                steps['up'].side_effect = start_services
                steps['finalize_setup'].side_effect = open_wizard
                install(data, SimpleNamespace(config=None))

            groups = [args[1:] for args, _ in steps['up'].call_args_list if len(args) > 1]
            self.assertEqual(groups, [
                ('postgres', 'synapse', 'context'),
                ('monitor', 'private-edge', 'dot'),
            ])
            steps['choose_option'].assert_not_called()
            steps['password'].assert_not_called()
            steps['finalize_setup'].assert_called_once()
            steps['prepare_graph_password'].assert_called_once_with(data, None, interactive=True)
            registry = load(data / 'agents.json')
            self.assertEqual(registry['agents'], {})
            self.assertTrue(load(data / 'onboarding.json')['select_agents'])
            self.assertTrue(steps['provision_layout'].call_args.kwargs['office_setup'])
            steps['run_telegram'].assert_called_once_with(data, 'start-guide')
            self.assertIn('Workspace deployed.', output.getvalue())

    def test_config_install_provisions_the_declared_roster_without_opening_element(self):
        config = {**self.raw, 'systemd': False, 'agents': ['manager'], 'owner_password_file': '/unused/password'}
        config.pop('subscriptions')
        with TemporaryDirectory() as work, redirect_stdout(StringIO()):
            path = Path(work)
            data, settings_file = path / 'workspace', path / 'install.json'
            save(settings_file, config)
            with patch.multiple(
                'installer', interactive=DEFAULT, password=DEFAULT, prepare_storage=DEFAULT,
                command_output=DEFAULT, compose_config=DEFAULT, compose=DEFAULT, service_units=DEFAULT,
                write_config=DEFAULT, up=DEFAULT, provision_layout=DEFAULT, prepare_images=DEFAULT,
                sync_workshop=DEFAULT, publish_inventory=DEFAULT, configure_network=DEFAULT,
                finalize_setup=DEFAULT, prepare_graph_password=DEFAULT, run_telegram=DEFAULT,
            ) as steps, patch('setup_guide.interactive_terminal', return_value=True):
                steps['password'].return_value = 'fixture-matrix-password'
                steps['command_output'].return_value = '[]'
                install(data, SimpleNamespace(config=settings_file))
            steps['interactive'].assert_not_called()
            steps['finalize_setup'].assert_not_called()
            self.assertEqual(list(load(data / 'agents.json')['agents']), ['manager'])
            self.assertTrue(steps['provision_layout'].call_args.kwargs['unattended'])
            self.assertFalse(steps['provision_layout'].call_args.kwargs['office_setup'])
            steps['run_telegram'].assert_called_once_with(data, 'start-guide')

    def test_telegram_activation_and_layout_repair_share_the_linking_flow(self):
        with TemporaryDirectory() as work, redirect_stdout(StringIO()):
            data = Path(work)
            rooms = {'space': '!agents:fixture.test', 'owner': '@operator:fixture.test'}
            save(data / 'rooms.json', rooms)
            for action in ('activate', 'layout'):
                with self.subTest(action=action), patch.multiple(
                    'installer', open_existing=DEFAULT, password=DEFAULT, login=DEFAULT, request=DEFAULT,
                    run_telegram=DEFAULT, provision_telegram_layout=DEFAULT, sync_workshop=DEFAULT,
                    compose_config=DEFAULT, compose=DEFAULT, docker_build=DEFAULT,
                ) as steps:
                    steps['open_existing'].return_value = (self.settings, self.registry)
                    steps['login'].return_value = {'access_token': 'fixture-token'}
                    steps['provision_telegram_layout'].return_value = {'bridge': '!bridge:fixture.test'}
                    telegram(data, [action, '--owner-password-file', '/fixture/matrix-pass'])
                steps['password'].assert_called_once_with(self.settings, Path('/fixture/matrix-pass'))
                steps['provision_telegram_layout'].assert_called_once_with(self.settings, data, rooms, 'fixture-token', None)
                expected = [('activate',), ('start-guide',)] if action == 'activate' else [
                    ('start-guide', '--refresh-bridge')]
                self.assertEqual([call.args[1:] for call in steps['run_telegram'].call_args_list], expected)
                steps['sync_workshop'].assert_not_called()

    def test_adding_a_remote_worker_keeps_the_telegram_workshop_network(self):
        data = Path('/srv/fixture')
        secrets = {'POSTGRES_PASSWORD': 'fixture'}
        early = definition(self.settings, data, {'agents': {}}, secrets)
        register_entry(self.settings, self.registry, 'remote-worker', self.presets['remote-worker'])
        later = definition(self.settings, data, self.registry, secrets)
        self.assertEqual(early['networks']['workshop-targets'], later['networks']['workshop-targets'])
        self.assertTrue(later['networks']['workshop-targets']['internal'])
        self.assertIn('workshop-egress', later['services']['persona-workshop']['networks'])
        self.assertNotIn('workshop-egress', later['services']['dot']['networks'])
        legacy = definition(self.settings, data, self.registry, secrets, workshop_internal=False)
        self.assertFalse(legacy['networks']['workshop-targets']['internal'])
        self.assertNotIn('workshop-egress', legacy['networks'])

    def test_telegram_office_socket_is_only_mounted_in_the_coordinator_connector(self):
        self.registry['agents']['task-manager'] = {**self.registry['agents']['manager']}
        data = Path('/srv/fixture')
        secrets = {'POSTGRES_PASSWORD': 'fixture'}
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                services = definition({**self.settings, 'telegram': enabled}, data, self.registry, secrets)['services']
                mounted = {key: item for key, service in services.items() for item in service.get('volumes', [])
                           if item['target'] == '/telegram-office'}
                if enabled:
                    self.assertEqual(set(mounted), {'task-manager'})
                    self.assertTrue(mounted['task-manager']['read_only'])
                    self.assertFalse(mounted['task-manager']['bind']['create_host_path'])
                    self.assertEqual(services['task-manager']['environment']['TELEGRAM_OFFICE_SOCKET'],
                                     '/telegram-office/actions.sock')
                else:
                    self.assertEqual(mounted, {})

    def test_catalog_keeps_unconfigured_agents_pending_during_health_refresh(self):
        observed = [{'name': 'Coder', 'status': 'Available'}]
        for inventory in ([], observed):
            with self.subTest(inventory=inventory):
                roster = document(self.registry, inventory, self.settings['server_name'], 100)['agents']
                coder = next(row for row in roster if row['key'] == 'coder')
                self.assertEqual(coder['status'], 'Sign-in required')
                self.assertFalse(coder['active'])

    def test_batch_sign_in_resumes_with_pending_local_agents(self):
        self.registry['agents']['vps-manager']['active'] = True
        self.registry['agents']['chief-of-staff']['active'] = True
        register_entry(self.settings, self.registry, 'remote-worker', self.presets['remote-worker'])
        with TemporaryDirectory() as work, redirect_stdout(StringIO()):
            data = Path(work)
            registry_path = data / 'agents.json'
            save(registry_path, self.registry)

            def activate(data, key, *, pending_only):
                self.assertTrue(pending_only)
                if key == 'coder':
                    raise CalledProcessError(1, ['fixture-sign-in'])
                registry = load(registry_path)
                registry['agents'][key]['active'] = True
                save(registry_path, registry)

            with patch('installer.agent_activate', side_effect=activate) as connect:
                with self.assertRaises(CalledProcessError):
                    agent_sign_in(data)
                self.assertEqual([call.args[1] for call in connect.call_args_list], ['manager', 'coder'])
            with patch('installer.agent_activate') as connect:
                agent_sign_in(data)
                connect.assert_called_once_with(data, 'coder', pending_only=True)
                connect.reset_mock()
                agent_sign_in(data, 'manager')
                connect.assert_called_once_with(data, 'manager')

    def test_repeat_setup_retains_active_agents_and_provider_choice(self):
        self.registry['agents']['coder']['active'] = True
        configured = {'provider': 'provider-anthropic', 'model': 'claude-fixture', 'effort': None}
        self.registry['agents']['coder']['amplifier'] = configured
        register_entry(self.settings, self.registry, 'coder', self.presets['coder'])
        self.assertTrue(self.registry['agents']['coder']['active'])
        self.assertEqual(self.registry['agents']['coder']['amplifier'], configured)
        self.assertEqual(self.registry['agents']['coder']['subscription'], 'Personal')
        self.assertEqual(self.registry['agents']['manager']['subscription'], 'Work')
        self.assertNotIn('analyst', self.registry['agents'])
        with self.assertRaises(ValueError):
            normalized({**self.raw, 'agents': ['manager', 'manager']}, self.presets)
        with self.assertRaises(ValueError):
            normalized({**self.raw, 'owner': 'coder'}, self.presets)

    def test_activation_records_only_the_verified_provider_model(self):
        metadata = {'provider': 'provider-anthropic', 'model': 'claude-fixture', 'effort': None}
        with TemporaryDirectory() as work, patch('installer.open_existing', return_value=(self.settings, self.registry)), \
             patch('installer.configure_amplifier', return_value=metadata) as configure, \
             patch('installer.compose') as compose, patch('installer.up'), patch('installer.sync_workshop'), \
             patch('installer.publish_inventory'):
            data = Path(work)
            compose.side_effect = CalledProcessError(1, ['probe'])
            with self.assertRaises(CalledProcessError):
                agent_activate(data, 'coder')
            self.assertFalse(self.registry['agents']['coder']['active'])
            self.assertNotIn('amplifier', self.registry['agents']['coder'])
            compose.side_effect = None
            agent_activate(data, 'coder')
            configure.assert_called_with(data, 'engine-coder', self.registry['agents']['coder'])
            self.assertTrue(load(data / 'agents.json')['agents']['coder']['active'])
        inventory = [{'name': 'Coder', 'model': 'gpt-6.2-sol', 'effort': 'medium'}]
        rows = document(self.registry, inventory, 'fixture.test', 100)['agents']
        coder = next(row for row in rows if row['key'] == 'coder')
        self.assertEqual((coder['model'], coder['effort']), ('claude-fixture', None))

    def test_local_topology_isolates_engine_credentials_and_desktop(self):
        doc = definition(self.settings, Path('/srv/fixture'), self.registry, {'POSTGRES_PASSWORD': 'fixture'})
        services = doc['services']
        desktop = [v for v in services['engine-coder']['volumes'] if v['target'] == '/workspace/desktop']
        self.assertEqual(len(desktop), 1)
        self.assertFalse(desktop[0]['read_only'])
        for key in ('vps-manager', 'chief-of-staff'):
            self.assertFalse(any(v['target'].endswith('/desktop') for v in services['engine-' + key]['volumes']))
        self.assertTrue(doc['networks']['acp-coder']['internal'])
        self.assertNotIn('memory', services['engine-coder']['networks'])
        self.assertIn('memory', services['coder']['networks'])
        self.assertIn('acp-coder', services['persona-workshop']['networks'])
        self.assertNotIn('memory', services['persona-workshop']['networks'])
        self.assertNotIn('ports', services['persona-workshop'])
        self.assertTrue(self.settings['context_url'].startswith('https://fixture.test:8444/browser/?connectURL='))
        self.assertTrue(all(service['restart'] == 'unless-stopped' for service in services.values()))
        for service in ('synapse', 'private-edge'):
            self.assertTrue(all(p.startswith('127.0.0.1:') for p in services[service]['ports']))

    def test_remote_mount_uses_same_remote_credential_scope(self):
        data = Path('/srv/fixture')
        config = {'key': 'coder', 'entry': self.registry['agents']['coder'], 'server_name': 'fixture.test',
                  'matrix_url': 'https://fixture.test', 'owner': '@operator:fixture.test',
                  'catalog': 'https://fixture.test:8443/agents.json', 'context': 'https://fixture.test:8445'}
        services = compose_remote(data, config, Path('/mnt/desktop'))['services']
        self.assertIn('/mnt/desktop', [v['source'] for v in services['engine']['volumes']])
        self.assertNotIn('ports', services['engine'])
        self.assertTrue(all(service['restart'] == 'unless-stopped' for service in services.values()))
        self.assertEqual(services['coder']['environment']['CONTEXT_INTELLIGENCE_URL'], config['context'])
        managed = compose_remote(data, {**config, 'workshop_host': 'worker.fixture.ts.net'}, Path('/mnt/desktop'))
        self.assertEqual(managed['services']['engine']['ports'], ['127.0.0.1:19444:19443'])
        self.assertIn('worker.fixture.ts.net', managed['services']['engine']['environment']['AGENT_INSTANCE_CERTIFICATE_NAMES'])

    def test_atomic_registry_and_url_validation(self):
        with TemporaryDirectory() as work:
            path = Path(work) / 'agents.json'
            save(path, self.registry)
            self.assertEqual(json.loads(path.read_text()), self.registry)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            normalized({**self.raw, 'context_url': 'https://fixture.test/?key=private'}, self.presets)
        with self.assertRaises(ValueError):
            normalized({**self.raw, 'context_url': 'https://fixture.test/?connectURL=bolt%2Bs%3A%2F%2Fother.test%3A17687'}, self.presets)

    def test_status_without_sudo_uses_only_dashboard_snapshots(self):
        with TemporaryDirectory() as work:
            data = Path(work)
            dashboard = data / 'dashboard'
            dashboard.mkdir()
            save(dashboard / 'agents.json', {'time': 100, 'agents': [{'name': 'Coder', 'status': 'Available'}]})
            save(dashboard / 'integrations.json', {'time': 290, 'services': {'Matrix': 'running'}})
            command = ['constellation', '--data-dir', work, 'status']
            output = StringIO()
            with patch('sys.argv', command), patch('installer.os.geteuid', return_value=1000), \
                 patch('installer.time.time', return_value=300), patch('installer.open_existing') as open_existing, \
                 patch('installer.compose') as compose, redirect_stdout(output):
                main()
            open_existing.assert_not_called()
            compose.assert_not_called()
            self.assertIn('Agents (stale, last updated', output.getvalue())
            self.assertIn('Coder  Available', output.getvalue())
            self.assertIn('Integrations (updated', output.getvalue())
            self.assertIn('Matrix  running', output.getvalue())

    def test_standard_docker_management_requires_sudo(self):
        command = ['constellation_matrix', 'status', '--refresh']
        error = StringIO()
        with patch('sys.argv', command), patch('installer.os.geteuid', return_value=1000), \
             patch('installer.compose') as compose, redirect_stderr(error):
            with self.assertRaises(SystemExit):
                main()
        self.assertIn('with sudo', error.getvalue())
        compose.assert_not_called()

    def test_old_rootless_data_is_not_opened_by_standard_docker(self):
        with TemporaryDirectory() as work:
            save(Path(work) / 'settings.json', {'rootless': True})
            command = ['constellation_matrix', '--data-dir', work, 'install']
            with patch('sys.argv', command), patch('installer.os.geteuid', return_value=0), \
                 patch('installer.install') as install, redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit):
                    main()
            install.assert_not_called()

    def test_dashboard_assets_are_readable_by_an_unprivileged_server(self):
        with TemporaryDirectory() as work, patch('installer.os.chown'):
            data = Path(work)
            prepare_storage(self.settings, data)
            for name in ('index.html', 'dashboard.mjs', 'dashboard.css', 'links.json'):
                self.assertEqual((data / 'dashboard' / name).stat().st_mode & 0o777, 0o644)

    def test_workspace_lock_reports_contention_and_waits(self):
        from fcntl import LOCK_EX, LOCK_NB

        with TemporaryDirectory() as work, redirect_stderr(StringIO()) as output, \
             patch('installer.fcntl.flock', side_effect=[BlockingIOError(), None]) as flock:
            with operator_lock(Path(work)):
                self.assertIn('Another Constellation command is updating this workspace', output.getvalue())
                self.assertIn('Workspace lock acquired', output.getvalue())
            self.assertEqual([item.args[1] for item in flock.call_args_list], [LOCK_EX | LOCK_NB, LOCK_EX])

    def test_agent_setup_explains_that_it_resumes_after_the_workspace_lock(self):
        with TemporaryDirectory() as work, redirect_stderr(StringIO()) as output, \
             patch('installer.fcntl.flock', side_effect=[BlockingIOError(), None]), \
             patch('installer.open_existing', return_value=(self.settings, {'agents': {'coder': {'active': True}}})), \
             patch('installer.configure_amplifier') as configure:
            agent_activate(Path(work), 'coder', pending_only=True)

        self.assertIn('Provider setup for coder is waiting for another workspace command.', output.getvalue())
        self.assertIn('Setup will continue automatically.', output.getvalue())
        configure.assert_not_called()

    def test_status_refresh_republishes_dashboard_assets_from_the_checkout(self):
        with TemporaryDirectory() as work:
            data = Path(work)
            (data / 'dashboard').mkdir()
            save(data / 'dashboard/index.html', 'older dashboard')
            command = ['constellation_matrix', '--data-dir', work, 'status', '--refresh']
            with patch('sys.argv', command), patch('installer.os.geteuid', return_value=0), \
                 patch('installer.open_existing', return_value=(self.settings, {'connection_history': {}})), \
                 patch('installer.publish_inventory'), patch('installer.refresh_connections'), patch('installer.compose'), \
                 patch('installer.run_telegram') as telegram:
                main()
            telegram.assert_called_once_with(data.resolve(), 'metrics')
            self.assertIn('connection-focus', (data / 'dashboard/index.html').read_text())
            self.assertEqual(load(data / 'dashboard/links.json')['context'], self.settings['context_url'])


if __name__ == '__main__':
    unittest.main()
