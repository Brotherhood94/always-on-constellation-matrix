"""Boot registration and project-scoped removal checks."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from host_services import enable_host_dependencies, enable_services, enable_telegram_units, remove_units, units
from dot import connect as connect_dot
from installer import PRESETS, load, normalized, save
from serve import Route, configure, remove_matching
from uninstall import uninstall


class LifecycleTest(TestCase):
    def test_boot_enables_runtime_and_only_selected_host_dependencies(self):
        with patch('host_services.run') as run:
            enable_host_dependencies({'tailscale': False})
            enable_host_dependencies({'tailscale': True})
            enable_services({'project': 'fixture'})
        self.assertEqual(run.call_args_list, [
            call('systemctl', 'enable', '--now', 'docker.service', 'containerd.service'),
            call('systemctl', 'enable', '--now', 'docker.service', 'containerd.service', 'tailscaled.service'),
            call('systemctl', 'enable', '--now', 'fixture-stack.service', 'fixture-monitor.service', 'fixture-backup.timer'),
        ])

    def test_host_units_and_telegram_timers_are_scoped_to_the_project(self):
        with TemporaryDirectory() as work, patch('host_services.run') as run:
            root = Path(work)
            unit_dir, data = root / 'system', root / 'workspace'
            unit_dir.mkdir()
            data.mkdir()
            with patch('host_services.UNITS', unit_dir):
                settings = {'project': 'fixture', 'systemd': True, 'telegram': True}
                units(settings, data, broker=True)
                enable_telegram_units(settings, data)
                stack = (unit_dir / 'fixture-stack.service').read_text()
                self.assertIn('Requires=docker.service', stack)
                self.assertIn('docker compose --profile agents start', stack)
                self.assertIn('WantedBy=multi-user.target', stack)
                monitor = (unit_dir / 'fixture-monitor.service').read_text()
                self.assertIn('After=docker.service fixture-stack.service', monitor)
                self.assertIn('Restart=on-failure', monitor)
                self.assertIn('WantedBy=sockets.target', (unit_dir / 'fixture-root.socket').read_text())
                self.assertIn('Persistent=true', (unit_dir / 'fixture-backup.timer').read_text())
                self.assertIn(f'TELEGRAM_STACK_DIR={data}', (unit_dir / 'fixture-telegram-backup.service').read_text())
                self.assertIn(f'ConditionPathExists={data}/backups/config', (unit_dir / 'fixture-telegram-backup.service').read_text())
                other = unit_dir / 'other-monitor.service'
                other.write_text('Description=Constellation VPS resource collector\n')
                replacement = unit_dir / 'fixture-dot.service'
                replacement.write_text('Description=Operator-owned service\n')
                remove_units('fixture')
                remove_units('fixture')
                self.assertTrue(other.exists())
                self.assertTrue(replacement.exists())
                self.assertTrue((unit_dir / 'matrix-workload.slice').exists())
                self.assertFalse((unit_dir / 'fixture-telegram-backup.timer').exists())
            run.assert_any_call('systemctl', 'enable', '--now', 'fixture-root.socket')
            run.assert_any_call('systemctl', 'enable', '--now', 'fixture-telegram-backup.timer', 'fixture-telegram-metrics.timer')
            self.assertEqual(sum('disable' in command.args for command in run.call_args_list), 1)

    def test_dot_tunnel_stays_a_boot_enabled_host_service(self):
        with TemporaryDirectory() as work, patch('dot.run') as run, \
             patch('host_services.UNITS', Path(work)), patch('dot.Path.is_socket', return_value=True), \
             patch('dot.install_client'), patch('dot.getpass', return_value='sk-fixture'):
            data = Path(work)
            connect_dot({'project': 'fixture', 'dot': True, 'systemd': True}, data, 'tunnel_' + '1' * 32)
            service = (data / 'fixture-dot.service').read_text()
            self.assertIn('WantedBy=multi-user.target', service)
            self.assertIn('After=network-online.target docker.service fixture-stack.service', service)
            self.assertIn('Restart=on-failure', service)
            self.assertIn('LoadCredential=runtime-key:', service)
            self.assertNotIn('sk-fixture', service)
            run.assert_any_call('systemctl', 'enable', '--now', 'fixture-dot.service')

    def test_uninstall_stops_auxiliary_projects_before_matrix_and_retains_data(self):
        with TemporaryDirectory() as work:
            data = Path(work)
            save(data / 'settings.json', {'project': 'fixture'})
            save(data / 'compose.yaml', {'name': 'fixture'})
            save(data / 'desktop-compose.yaml', {'name': 'fixture-desktop'})
            save(data / 'rooms.json', {'office': '!fixture'})
            (data / '.env').touch()
            (data / 'telegram').mkdir()
            (data / 'backups').mkdir()
            with patch('uninstall.run') as run, patch('uninstall.compose') as compose, \
                 patch('uninstall.remove_units') as units, patch('uninstall.remove_workspace') as serve:
                uninstall(data)
            units.assert_called_once_with('fixture')
            self.assertIn('fixture-telegram', run.call_args_list[0].args)
            self.assertEqual(run.call_args_list[1].args[-3:], (str(data / 'desktop-compose.yaml'), 'down', '--remove-orphans'))
            compose.assert_called_once_with(data, '--profile', 'agents', 'down', '--remove-orphans')
            serve.assert_called_once()
            self.assertTrue((data / 'backups').is_dir())
            self.assertEqual(load(data / 'rooms.json'), {'office': '!fixture'})
            save(data / 'compose.yaml', {'name': 'another-project'})
            with patch('uninstall.remove_units') as units, self.assertRaisesRegex(ValueError, 'does not match'):
                uninstall(data)
            units.assert_not_called()

    def test_tailnet_removal_matches_targets_and_keeps_neighbor_paths(self):
        config = {'Web': {'fixture.test:8443': {'Handlers': {
            '/': {'Path': '/srv/fixture/dashboard'}, '/other': {'Proxy': 'http://localhost:4000'},
        }}}, 'TCP': {'17687': {'TCPForward': '127.0.0.1:17688', 'TerminateTLS': 'fixture.test'}}}
        routes = [Route(8443, '/srv/fixture/dashboard'), Route(8443, 'http://127.0.0.1:4999', path='/other'),
                  Route(17687, 'tcp://127.0.0.1:17688', 'tls-terminated-tcp')]
        with patch('serve.status', return_value=config), patch('serve.run') as run:
            remove_matching('fixture.test', routes)
        self.assertEqual(run.call_args_list, [
            call('tailscale', 'serve', '--bg', '--https=8443', '--set-path=/', 'off'),
            call('tailscale', 'serve', '--bg', '--tls-terminated-tcp=17687', 'off'),
        ])

    def test_remote_worker_uninstall_is_local_and_preserves_mount(self):
        with TemporaryDirectory() as work:
            data = Path(work)
            desktop = data / 'desktop'
            desktop.mkdir()
            save(data / 'compose.yaml', {'name': 'constellation-remote-fixture', 'services': {
                'engine': {'ports': ['127.0.0.1:19444:19443']}, 'fixture': {},
            }})
            with patch('uninstall.compose') as compose, patch('uninstall.remove_units') as units, \
                 patch('uninstall.remove_matching') as serve, \
                 patch('uninstall.command_output', return_value='{"Self":{"DNSName":"fixture.test."}}'):
                uninstall(data)
            compose.assert_called_once_with(data, 'down', '--remove-orphans')
            serve.assert_called_once_with('fixture.test', [Route(19444, 'tcp://127.0.0.1:19444', 'tcp')])
            units.assert_not_called()
            self.assertTrue(desktop.is_dir())

    def test_tailnet_install_records_routes_and_replaces_only_prior_ports(self):
        raw = {'server_name': 'fixture.test', 'owner': 'operator', 'agents': [], 'subscriptions': {'default': 'Work'}}
        settings = normalized(raw, load(PRESETS))
        registry = {'agents': {}}
        with TemporaryDirectory() as work:
            data = Path(work)
            tailnet = json.dumps({'Self': {'DNSName': 'fixture.test.'}, 'CertDomains': ['fixture.test']})
            config = {'Web': {'fixture.test:8443': {'Handlers': {'/': {'Path': str(data / 'dashboard')}}}}}
            with patch('serve.command_output', return_value=tailnet), patch('serve.status', return_value=config), \
                 patch('serve.run') as run:
                configure({**settings, 'dashboard_https_port': 9443}, data, registry, previous=settings)
            run.assert_any_call('tailscale', 'serve', '--bg', '--https=9443', '--set-path=/', str(data / 'dashboard'))
            run.assert_any_call('tailscale', 'serve', '--bg', '--https=8443', '--set-path=/', 'off')
            recorded = load(data / 'serve-routes.json')
            self.assertIn(9443, {route['port'] for route in recorded})
            self.assertNotIn(8443, {route['port'] for route in recorded})

    def test_tailnet_conflict_keeps_the_previous_routes_available_for_uninstall(self):
        raw = {'server_name': 'fixture.test', 'owner': 'operator', 'agents': [], 'subscriptions': {'default': 'Work'}}
        settings = normalized(raw, load(PRESETS))
        tailnet = json.dumps({'Self': {'DNSName': 'fixture.test.'}, 'CertDomains': ['fixture.test']})
        config = {'Web': {'fixture.test:9443': {'Handlers': {'/': {'Proxy': 'http://localhost:5000'}}}}}
        with TemporaryDirectory() as work, patch('serve.command_output', return_value=tailnet), \
             patch('serve.status', return_value=config), patch('serve.run') as run:
            data = Path(work)
            with self.assertRaisesRegex(ValueError, 'another target'):
                configure({**settings, 'dashboard_https_port': 9443}, data, {'agents': {}}, previous=settings)
            self.assertIn(8443, {route['port'] for route in load(data / 'serve-routes.json')})
            run.assert_not_called()
