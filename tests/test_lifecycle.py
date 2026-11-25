"""Boot registration and project-scoped removal checks."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from host_services import (enable_host_dependencies, enable_services, enable_telegram_units, remove_units, roster_units,
                           stage_roster_runtime, units)
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
            self.assertEqual(run.call_args_list[-4:], [
                call('systemctl', 'disable', '--now', 'fixture-telegram-backup.timer',
                     'fixture-telegram-metrics.timer', 'fixture-backup.timer', 'fixture-root.socket'),
                call('systemctl', 'stop', 'fixture-telegram-backup.service', 'fixture-telegram-metrics.service',
                     'fixture-backup.service', 'fixture-root.service'),
                call('systemctl', 'disable', '--now', 'fixture-monitor.service', 'fixture-stack.service'),
                call('systemctl', 'daemon-reload'),
            ])

    def test_roster_socket_and_retry_timer_are_scoped_and_removed_on_uninstall(self):
        with TemporaryDirectory() as work, patch('host_services.run') as run, \
             patch('host_services.stage_roster_runtime', return_value=Path('/srv/fixture/host/roster-runtime')):
            path = Path(work)
            with patch('host_services.UNITS', path):
                roster_units({'project': 'fixture'}, Path('/srv/fixture'))
                socket = (path / 'fixture-roster.socket').read_text()
                service = (path / 'fixture-roster@.service').read_text()
                self.assertIn('ListenStream=/run/fixture-roster.sock', socket)
                self.assertIn('SocketMode=0660', socket)
                self.assertIn('StandardInput=socket', service)
                self.assertIn('StandardOutput=journal', service)
                self.assertIn('/srv/fixture/host/roster-runtime/deploy/roster_broker.py', service)
                self.assertIn('OnUnitActiveSec=30s', (path / 'fixture-roster-reconcile.timer').read_text())
                remove_units('fixture')
                self.assertEqual(list(path.iterdir()), [])
            run.assert_any_call('systemctl', 'enable', '--now', 'fixture-roster.socket', 'fixture-roster-reconcile.timer')
            run.assert_any_call('systemctl', 'stop', 'fixture-roster@*.service')

    def test_roster_runtime_copies_code_into_private_host_state(self):
        with TemporaryDirectory() as work:
            data = Path(work)
            runtime = stage_roster_runtime(data)
            self.assertEqual(runtime.parent, data / 'host')
            self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)
            self.assertTrue((runtime / 'deploy/roster_broker.py').is_file())
            self.assertTrue((runtime / 'agents/connector/Dockerfile').is_file())
            self.assertFalse((runtime / 'agents/connector/node_modules').exists())

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

    def test_purge_removes_the_deployment_and_scoped_leftover_resources(self):
        with TemporaryDirectory() as work:
            root = Path(work).resolve()
            data = root / 'workspace'
            data.mkdir()
            save(data / 'settings.json', {'project': 'fixture', 'server_name': 'fixture.test'})
            save(data / 'agents.json', {'local_server': 'fixture.test', 'agents': {}})
            save(data / 'compose.yaml', {'name': 'fixture'})
            outside = root / 'telegram-api.json'
            outside.touch()
            mounts = root / 'mountinfo'
            mounts.write_text('1 0 0:1 / / rw - tmpfs tmpfs rw\n')

            with patch('uninstall.MOUNT_INFO', mounts), patch('uninstall.compose') as compose, \
                 patch('uninstall.remove_units'), patch('uninstall.remove_workspace'), \
                 patch('uninstall.run') as run, patch('uninstall.command_output') as listing:
                listing.side_effect = ['leftover-container', 'leftover-volume', 'leftover-network', *([''] * 6)]
                uninstall(data, purge=True)
                uninstall(data, purge=True)

            compose.assert_called_once_with(data, '--profile', 'agents', 'down', '--remove-orphans', '--volumes')
            self.assertEqual(run.call_args_list, [
                call('docker', 'rm', '--force', '--volumes', 'leftover-container'),
                call('docker', 'volume', 'rm', 'leftover-volume'),
                call('docker', 'network', 'rm', 'leftover-network'),
            ])
            for project in ('fixture', 'fixture-telegram', 'fixture-desktop'):
                listing.assert_any_call('docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={project}')
            self.assertTrue(outside.is_file())
            self.assertFalse(data.exists())

    def test_purge_preserves_mounted_data(self):
        with TemporaryDirectory() as work:
            root = Path(work).resolve()
            data = root / 'workspace'
            desktop = data / 'shared desktop'
            desktop.mkdir(parents=True)
            mount = str(desktop).replace(' ', r'\040')
            mounts = root / 'mountinfo'
            mounts.write_text(f'2 1 0:1 / {mount} rw - tmpfs tmpfs rw\n')
            with patch('uninstall.MOUNT_INFO', mounts), patch('uninstall.compose') as compose, \
                 self.assertRaisesRegex(ValueError, 'Unmount .*shared desktop'):
                uninstall(data, purge=True)
            compose.assert_not_called()
            self.assertTrue(desktop.is_dir())

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
