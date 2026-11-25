"""Installer and topology checks using deployment-independent fixtures."""
from contextlib import redirect_stderr, redirect_stdout
import curses
from io import StringIO
import json
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy'))
from agent_picker import agent_checklist, choose_option, menu, select_agents, select_integrations, text_page
from installer import (
    PRESETS, agent_activate, agent_sign_in, interactive, load, main,
    normalized, password, prepare_storage, register_entry, save, show_element_setup, tailscale_dns,
)
from agent_catalog import document
from compose import definition
from remote import compose_remote


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.presets = load(PRESETS)
        self.raw = {
            'server_name': 'fixture.test', 'owner': 'operator', 'agents': ['manager', 'coder', 'root', 'chief-of-staff'],
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

    def test_password_page_masks_input_and_keeps_spaces_on_retry(self):
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        fixture = '  synthetic contraseña  '
        screen.get_wch.side_effect = [*'short', '\n', *([curses.KEY_BACKSPACE] * 5), *fixture, '\n']
        with patch('agent_picker.terminal_picker', side_effect=lambda render, *args, **kwargs:
                   render(screen, *args, **kwargs)):
            self.assertEqual(password({}), fixture)
        rendered = '\n'.join(call.args[2] for call in screen.addnstr.call_args_list)
        self.assertIn('Matrix operator password', rendered)
        self.assertIn('Use an operator password of at least 12 characters', rendered)
        self.assertIn('*' * len(fixture), rendered)
        self.assertNotIn('short', rendered)
        self.assertNotIn('synthetic', rendered)

    def test_password_fallback_uses_getpass(self):
        fixture = '  synthetic-password  '
        with patch('agent_picker.terminal_picker', return_value=None), \
             patch('agent_picker.getpass', return_value=fixture) as getpass, \
             patch('builtins.input') as line_input:
            self.assertEqual(password({}), fixture)
        getpass.assert_called_once_with('Matrix operator password: ')
        line_input.assert_not_called()

    def test_password_file_bypasses_the_screen_and_requires_private_permissions(self):
        with TemporaryDirectory() as work, patch('installer.prompt_text') as prompt:
            path = Path(work) / 'operator-password'
            fixture = '  synthetic-password  '
            path.write_text(fixture + '\n')
            path.chmod(0o600)
            self.assertEqual(password({'owner_password_file': str(path)}), fixture)
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'must be private'):
                password({'owner_password_file': str(path)})
        prompt.assert_not_called()

    def test_interactive_installer_requests_username_directly(self):
        with patch('installer.choose_option', return_value='tailscale') as choose, \
             patch('installer.select_integrations', return_value=['element', 'telegram', 'dot']) as integrations, \
             patch('installer.select_agents', return_value=['coder', 'reviewer']) as picker, \
             patch('agent_picker.terminal_picker', return_value=None), \
             patch('installer.tailscale_dns', return_value='fixture.test'), \
             patch('builtins.input', return_value='my-operator') as answer:
            settings = normalized(interactive(self.presets, {}), self.presets)
        self.assertEqual([call.args[0] for call in choose.call_args_list], ['Choose private access for Matrix and the dashboard'])
        picker.assert_called_once_with(self.presets, ())
        integrations.assert_called_once_with(['telegram', 'dot'])
        answer.assert_called_once_with('Matrix operator username: ')
        self.assertEqual(settings['agents'], ['coder', 'reviewer'])
        self.assertNotIn('connection', settings)
        self.assertNotIn('subscriptions', settings)
        self.assertEqual(settings['owner'], 'my-operator')
        self.assertEqual(settings['server_name'], 'fixture.test')
        self.assertTrue(settings['tailscale'] and settings['dot'] and settings['telegram'])

    def test_interactive_installer_supports_custom_proxy_and_operator(self):
        with patch('installer.choose_option', return_value='proxy') as choose, \
             patch('installer.select_integrations', return_value=['element']), \
             patch('installer.select_agents', return_value=[]), patch('installer.tailscale_dns', return_value=None), \
             patch('agent_picker.terminal_picker', return_value=None), \
             patch('builtins.input', side_effect=['matrix.example', '', 'Coder', 'coder', 'my-operator']) as answer, \
             redirect_stdout(StringIO()):
            settings = normalized(interactive(self.presets, {}), self.presets)
        self.assertEqual(list(choose.call_args_list[0].args[1]), ['proxy'])
        self.assertEqual(answer.call_count, 5)
        self.assertFalse(settings['tailscale'])
        self.assertEqual(settings['server_name'], 'matrix.example')
        self.assertEqual(settings['owner'], 'my-operator')

    def test_interactive_rerun_keeps_the_existing_operator_username(self):
        with patch('installer.choose_option', return_value='tailscale'), \
             patch('installer.select_integrations', return_value=['element', 'telegram']) as integrations, \
             patch('installer.select_agents', return_value=[]), \
             patch('agent_picker.terminal_picker', return_value=None), \
             patch('installer.tailscale_dns', return_value='fixture.test'), \
             patch('builtins.input', side_effect=['different-operator', '']) as answer, redirect_stdout(StringIO()):
            saved = {'owner': 'my-operator', 'telegram': True, 'dot': False}
            settings = normalized(interactive(self.presets, saved), self.presets)
        self.assertEqual(settings['owner'], 'my-operator')
        integrations.assert_called_once_with(['telegram'])
        self.assertTrue(settings['telegram'])
        self.assertFalse(settings['dot'])
        self.assertEqual([call.args[0] for call in answer.call_args_list], ['Matrix operator username [my-operator]: '] * 2)

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
        self.assertIn('https://element.io/download', rendered)
        self.assertIn('https://fixture.test as your custom homeserver', rendered)
        self.assertIn('Sign in as operator', rendered)
        self.assertIn('Tailscale', rendered)
        self.assertIn('Agents → VPS Alerts → Pinned messages', rendered)
        self.assertIn(self.settings['dashboard_url'], rendered)
        self.assertIn(self.settings['context_url'], rendered)
        output = StringIO()
        with redirect_stdout(output):
            show_element_setup({**self.settings, 'tailscale': False})
        self.assertNotIn('Tailscale', output.getvalue())

    def test_legacy_connection_config_preserves_existing_agent_identity(self):
        raw = dict(self.raw)
        del raw['subscriptions']
        settings = normalized({**raw, 'connection': 'chatgpt-codex'}, self.presets)
        self.assertNotIn('connection', settings)
        register_entry(settings, self.registry, 'coder', self.presets['coder'])
        self.assertEqual(self.registry['agents']['coder']['subscription'], 'Personal')
        with self.assertRaises(ValueError):
            normalized({**raw, 'connection': 'unsupported'}, self.presets)

    def test_batch_sign_in_resumes_with_pending_local_agents(self):
        self.registry['agents']['root']['active'] = True
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
        for key in ('root', 'chief-of-staff'):
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


if __name__ == '__main__':
    unittest.main()
