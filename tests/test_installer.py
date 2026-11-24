"""Installer and topology checks using deployment-independent fixtures."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy'))
from installer import configure_network, load, main, normalized, prepare_storage, register_entry, PRESETS, save
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

    def test_rootless_commands_use_account_state_and_preserve_sign_in(self):
        with TemporaryDirectory() as work:
            data = Path(work).resolve() / 'constellation-matrix'
            data.mkdir()
            save(data / 'settings.json', {'rootless': True})
            with patch.dict('os.environ', {'XDG_STATE_HOME': work}), \
                 patch('installer.os.geteuid', return_value=1000), patch('installer.launch') as launch:
                for command in (['status', '--refresh'], ['agent', 'sign-in', 'manager']):
                    with patch('sys.argv', ['constellation_matrix', *command]):
                        main()
            self.assertEqual(launch.call_args_list, [
                call(ROOT, data, ['status', '--refresh'], [], tailnet=''),
                call(ROOT, data, ['agent', 'sign-in', 'manager'], [], tailnet=''),
            ])
            error = StringIO()
            save(data / 'settings.json', {})
            with patch('sys.argv', ['constellation_matrix', '--data-dir', str(data), 'status', '--refresh']), \
                 patch('installer.os.geteuid', return_value=1000), patch('installer.launch') as launch, \
                 redirect_stderr(error):
                with self.assertRaises(SystemExit):
                    main()
            launch.assert_not_called()
            self.assertIn('sudo for administrator state', error.getvalue())

    def test_rootless_settings_and_tailnet_dashboard_route(self):
        for changes in ({'agents': ['root']}, {'agents': ['remote-worker']}, {'dot': True}, {'systemd': True}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'administrator install'):
                normalized({**self.raw, 'dot': False, 'subscriptions': {'default': 'Work'}, **changes},
                           self.presets, rootless=True)
        settings = normalized({**self.raw, 'agents': ['coder'], 'dot': False}, self.presets, rootless=True)
        self.assertTrue(settings['rootless'])
        self.assertFalse(settings['systemd'])
        with self.assertRaisesRegex(ValueError, 'administrator install'):
            register_entry(settings, {'agents': {}}, 'custom', {**self.presets['coder'], 'placement': 'remote'})
        doc = definition(settings, Path('/srv/fixture'), {'agents': {}}, {'POSTGRES_PASSWORD': 'fixture'})
        dashboard = doc['services']['dashboard']
        self.assertEqual(dashboard['ports'], ['127.0.0.1:18082:8080'])
        self.assertTrue(dashboard['volumes'][0]['read_only'])
        info = json.dumps({'Self': {'DNSName': 'fixture.test.'}, 'CertDomains': ['fixture.test']})
        with patch('installer.command_output', side_effect=[info, '{}']), \
             patch('installer.load', return_value={'agents': {}}), patch('installer.run') as run:
            configure_network(settings, Path('/srv/fixture'))
        self.assertIn(call('tailscale', 'serve', '--bg', '--https=8443', 'http://127.0.0.1:18082'), run.call_args_list)

    def test_dashboard_assets_are_readable_by_an_unprivileged_server(self):
        with TemporaryDirectory() as work, patch('installer.os.chown'):
            data = Path(work)
            prepare_storage(self.settings, data)
            for name in ('index.html', 'dashboard.mjs', 'dashboard.css', 'links.json'):
                self.assertEqual((data / 'dashboard' / name).stat().st_mode & 0o777, 0o644)


if __name__ == '__main__':
    unittest.main()
