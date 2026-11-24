"""Installer and topology checks using deployment-independent fixtures."""
from contextlib import redirect_stderr, redirect_stdout
import curses
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy'))
from agent_picker import checklist, select_agents
from installer import interactive, load, main, normalized, prepare_storage, register_entry, PRESETS, save
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
            curses.KEY_DOWN, ord(' '), curses.KEY_DOWN, ord(' '),
            curses.KEY_UP, ord(' '), curses.KEY_DOWN, curses.KEY_DOWN, ord(' '), ord('\n'),
        ]
        self.assertEqual(checklist(screen, self.presets), ['reviewer', 'manager'])
        rendered = '\n'.join(call.args[2] for call in screen.addnstr.call_args_list)
        self.assertIn('[x] Coder', rendered)
        self.assertIn('Implement and test code changes.', rendered)
        self.assertIn('Role: coordinator', rendered)
        self.assertIn('Model: gpt-6.2-astra  |  Reasoning: high', rendered)

    def test_agent_checklist_scrolls_and_can_confirm_an_empty_roster(self):
        screen = Mock()
        screen.getmaxyx.return_value = (20, 65)
        screen.getch.side_effect = [curses.KEY_DOWN] * 5 + [ord(' '), ord(' '), curses.KEY_ENTER]
        self.assertEqual(checklist(screen, self.presets), [])
        rendered = '\n'.join(call.args[2] for call in screen.addnstr.call_args_list)
        self.assertIn('[x] Remote Worker', rendered)
        self.assertIn('Runs: remote', rendered)

    def test_agent_selection_can_use_line_input_without_a_terminal(self):
        output = StringIO()
        with patch('sys.stdin.isatty', return_value=False), patch('agent_picker.curses.wrapper') as picker, \
             patch('builtins.input', return_value=' coder, reviewer '), redirect_stdout(output):
            self.assertEqual(select_agents(self.presets), ['coder', 'reviewer'])
        picker.assert_not_called()
        self.assertIn('Analyze requirements, evidence and options', output.getvalue())

    def test_interactive_installer_only_asks_overrides_for_selected_agents(self):
        answers = ['Work', 'Personal', '', 'n', 'fixture.test', 'operator', 'n', 'n']
        with patch('installer.select_agents', return_value=['coder', 'reviewer']) as picker, \
             patch('builtins.input', side_effect=answers) as answer:
            settings = normalized(interactive(self.presets), self.presets)
        picker.assert_called_once_with(self.presets)
        self.assertEqual(settings['agents'], ['coder', 'reviewer'])
        self.assertEqual(settings['subscriptions'], {'default': 'Work', 'overrides': {'coder': 'Personal'}})
        prompts = [call.args[0] for call in answer.call_args_list if 'subscription override' in call.args[0]]
        self.assertEqual(prompts, ['coder subscription override (blank uses Work): ',
                                   'reviewer subscription override (blank uses Work): '])

    def test_repeat_setup_retains_active_agents_and_provider_choice(self):
        self.registry['agents']['coder']['active'] = True
        register_entry(self.settings, self.registry, 'coder', self.presets['coder'])
        self.assertTrue(self.registry['agents']['coder']['active'])
        self.assertEqual(self.registry['agents']['coder']['subscription'], 'Personal')
        self.assertEqual(self.registry['agents']['manager']['subscription'], 'Work')
        self.assertNotIn('analyst', self.registry['agents'])
        with self.assertRaises(ValueError):
            normalized({**self.raw, 'agents': ['manager', 'manager']}, self.presets)
        with self.assertRaises(ValueError):
            normalized({**self.raw, 'owner': 'coder'}, self.presets)

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
