"""Exercise the coordinator, checkpoint and worker lease boundaries without a live tailnet."""
from pathlib import Path
from http.server import ThreadingHTTPServer
from tempfile import TemporaryDirectory
from threading import Event, Thread
from urllib.error import HTTPError
from unittest import TestCase, mock
import io
import os
import subprocess
import sys
import tarfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
import mobility
import mobility_runtime
from mobility_transport import Channel, archive_grant, leased_agents, relay, root, secret_digest, serve, staged_agents
from mobility_worker import reconcile


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    mobility.save(path, value)


class Moves(TestCase):
    vps = 'vps.example.ts.net'
    first = 'first.example.ts.net'
    second = 'second.example.ts.net'

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.settings = {**mobility.PORTS, 'server_name': self.vps, 'project': 'isolated',
                         'owner': 'operator', 'tailscale': True, 'systemd': True, 'dot': False, 'telegram': False}
        put(self.data / 'settings.json', self.settings)
        self.entry = {'server': self.vps, 'placement': 'local', 'name': 'Helper', 'model': 'test-model',
                      'effort': 'low', 'profile': {'limits': 'Read only'}, 'active': True}
        put(self.data / 'agents.json', {'agents': {'helper': self.entry}, 'local_server': self.vps})
        put(self.data / 'context/helper.token', 'synthetic\n')
        put(self.data / 'support/guides.json', {'revision': 'test', 'documents': []})
        put(root(self.data) / 'endpoint.json', {'endpoint': 'https://100.100.100.100:18486', 'ca': 'synthetic'})
        for host in (self.first, self.second):
            put(root(self.data) / 'hosts' / (host + '.json'), {'host': host, 'node': host, 'seen': time.time()})
        self.running = self.vps
        self.events = []
        self.fail = None
        self.checked = None
        for patch in (
            mock.patch.object(mobility, 'open_existing', side_effect=lambda data: (self.settings, self.registry())),
            mock.patch.object(mobility, 'configure_network'),
            mock.patch.object(mobility, 'compose_config'),
            mock.patch.object(mobility, 'publish_inventory'),
            mock.patch.object(mobility, 'job', side_effect=self.job),
            mock.patch.object(mobility, 'wait_ready', side_effect=self.activate),
            mock.patch.object(mobility_runtime.Runtime, 'check_drain_support'),
            mock.patch.object(mobility_runtime.Runtime, 'warm'),
            mock.patch('installer.publish_helper_guides'),
            mock.patch('installer.sync_workshop', side_effect=self.workshop),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def registry(self):
        return mobility.load(self.data / 'agents.json')

    def workshop(self, settings, data, registry, reconnect):
        self.assertIsNone(self.running)
        self.assertEqual(reconnect, 'helper')
        self.assertEqual(registry['agents']['helper']['server'], self.checked)

    def phase(self, identifier):
        return mobility.load_move(self.data, identifier)['phase']

    def job(self, data, move, *, side, label, operation, transfer=None, details=None):
        host = move[side]
        self.events.append((operation, host))
        if operation == self.fail:
            self.fail = None
            return {'ok': False, 'code': 'provider_sign_in' if operation == 'restore' else 'fixture_failure'}
        if operation == 'prepare':
            self.assertIn(self.running, (move['source'], None))
        elif operation in ('checkpoint', 'recover'):
            self.assertIn(self.running, (host, None))
            self.running = None
            self.assertEqual(len(transfer), 32)
        elif operation in ('restore', 'verify'):
            self.assertIsNone(self.running)
            self.assertIsNotNone(details or move['target'])
            self.checked = host
            return {'ok': True, 'amplifier': {'model': 'verified-model'}, 'workshop_token': 'private', 'ca': 'synthetic'}
        return {'ok': True}

    def activate(self, data, move, host, details):
        self.assertIsNone(self.running)
        self.assertEqual(host, self.checked)
        active = self.registry()['agents']['helper']
        self.assertEqual(active['server'], host)
        self.assertEqual(active['mobility_generation'], details['generation'])
        self.running = host

    def move(self, target, **options):
        identifier = mobility.create_move(self.data, 'helper', target, yes=True, **options)
        mobility.execute_id(self.data, identifier)
        return identifier

    def test_roundtrip_and_first_activation_on_worker(self):
        registry = self.registry()
        registry['agents']['helper']['active'] = False
        put(self.data / 'agents.json', registry)
        self.running = None
        first = self.move(self.first, initial=True)
        second = self.move(self.second)
        third = self.move('vps')
        self.assertEqual(self.running, self.vps)
        self.assertEqual(self.registry()['agents']['helper']['amplifier']['model'], 'verified-model')
        for identifier in (first, second, third):
            self.assertEqual(self.phase(identifier), 'complete')
            move = mobility.load_move(self.data, identifier)
            self.assertNotIn('target', move)
            self.assertIn('finished', move)
            mobility.execute_id(self.data, identifier)
        self.assertEqual(self.events.count(('restore', self.second)), 1)

    def test_failed_preflight_leaves_source_and_can_resume(self):
        self.fail = 'prepare'
        identifier = mobility.create_move(self.data, 'helper', self.first, yes=True)
        with self.assertRaisesRegex(ValueError, 'fixture_failure'):
            mobility.execute_id(self.data, identifier)
        self.assertEqual(self.running, self.vps)
        self.assertEqual(self.events, [('prepare', self.first)])
        mobility.execute_id(self.data, identifier)
        self.assertEqual(self.running, self.first)

    def test_provider_failure_preserves_location_and_rollback_parks_candidate_before_source(self):
        self.fail = 'restore'
        identifier = mobility.create_move(self.data, 'helper', self.first, yes=True)
        with self.assertRaisesRegex(ValueError, 'sign-in'):
            mobility.execute_id(self.data, identifier)
        registry = self.registry()
        self.assertEqual(registry['agents']['helper']['server'], self.vps)
        self.assertEqual(leased_agents(registry, self.first), {})
        self.assertIn('helper', staged_agents(registry, self.first))
        self.fail = 'recover'
        with self.assertRaisesRegex(ValueError, 'fixture_failure'):
            mobility.execute_id(self.data, identifier, rollback=True)
        mobility.execute_id(self.data, identifier)
        self.assertEqual(self.running, self.vps)
        self.assertLess(self.events.index(('recover', self.first)), self.events.index(('restore', self.vps)))
        self.assertEqual(self.phase(identifier), 'rolled-back')


class WorkerSafety(TestCase):
    def test_dashboard_reports_verified_generation_and_redacts_the_transfer(self):
        from agent_catalog import placement_details
        with TemporaryDirectory() as tmp:
            data = Path(tmp)
            host = 'worker.example.ts.net'
            generation = 'a' * 32
            entry = {'server': host, 'placement': 'remote', 'mobility_generation': generation,
                     'mobility_move': 'pending'}
            put(root(data) / 'hosts' / (host + '.json'), {'seen': 100, 'agents': {'helper': {
                'generation': generation, 'ready': True}}})
            registry = {'relocations': {
                'old': {'agent': 'helper', 'finished': 90, 'created': 87, 'source': 'vps', 'destination': host,
                        'phase': 'complete', 'receipt': 'private'},
                'pending': {'id': 'pending', 'agent': 'helper', 'source': host, 'destination': 'vps',
                            'created': 101, 'phase': 'draining', 'target': {'rpc_token': 'private'}},
            }}
            snapshot = placement_details(registry, entry, 'helper', 110, data)
            self.assertEqual(snapshot['status'], 'Draining')
            self.assertEqual(snapshot['healthCheckedAt'], 100)
            self.assertEqual(snapshot['moveHistory'][0]['outcome'], 'complete')
            self.assertNotIn('private', str(snapshot))
            entry.pop('mobility_move')
            self.assertEqual(placement_details(registry, entry, 'helper', 110, data)['status'], 'Available')
            self.assertEqual(placement_details(registry, entry, 'helper', 161, data)['status'], 'Unavailable')

    def test_roster_relay_uses_the_coordinators_operator_approval_gate(self):
        with TemporaryDirectory() as tmp:
            data = Path(tmp)
            generation = 'a' * 32
            host = 'worker.example.ts.net'
            put(data / 'settings.json', {})
            registry = {'agents': {'coach': {'server': host, 'placement': 'remote', 'active': True,
                                           'mobility_generation': generation}}}
            request = {'agent': 'coach', 'generation': generation, 'operation': 'roster',
                       'payload': {'actor': 'hiring-manager', 'poll': '$fixture'}}
            with mock.patch('roster_broker.handle', return_value={'status': 'applied'}) as handle, \
                 mock.patch('installer.read_password_file', return_value='synthetic-private-key'):
                self.assertEqual(relay(data, registry, host, request), {'status': 'applied'})
                handle.assert_called_once_with(data, {'actor': 'coach', 'poll': '$fixture', 'key': 'synthetic-private-key'})
                for wrong in ({**request, 'generation': 'b' * 32}, {**request, 'operation': 'telegram-mcp'}):
                    with self.assertRaises(PermissionError):
                        relay(data, registry, host, wrong)

    def test_expired_lease_parks_containers_and_fenced_generation_cannot_restart(self):
        with TemporaryDirectory() as tmp:
            data = Path(tmp)
            details = {'key': 'helper', 'generation': 'a' * 32}
            put(data / 'worker-agents/helper/runtime.json', details)
            runtime = mock.MagicMock()
            runtime.root = data
            runtime.engine = 'engine-helper'
            runtime.connectors = ['helper', 'helper-dm']
            runtime.running.return_value = {'helper'}
            put(data / 'restored', details['generation'])
            with mock.patch('mobility_worker.worker_runtime', return_value=runtime):
                health = reconcile(data, {}, {}, {}, {}, None)
                self.assertFalse(health)
                runtime.command.assert_called_once_with('stop', '--timeout', '20', 'helper', 'helper-dm',
                                                        'engine-helper', timeout=90)
                runtime.command.reset_mock()
                put(data / 'fenced', details['generation'])
                self.assertEqual(reconcile(data, {}, {'helper': details['generation']}, {}, {}, None), {})
                runtime.start.assert_not_called()

    def test_checkpoint_keeps_private_state_excludes_shared_files_and_rejects_traversal(self):
        with TemporaryDirectory() as tmp:
            location = Path(tmp)
            source = location / 'source'
            state = location / 'destination'
            state.mkdir()
            paths = ['instance/session', 'instance/provider', 'matrix/matrix-token', 'matrix/connector.sqlite3',
                     'dm/connector.sqlite3', 'work/report.txt', 'work/desktop/live.txt',
                     'matrix/relocation-drain']
            for key in paths:
                put(source / key, key)
            archive = location / 'agent.tar.gz'
            mobility_runtime.checkpoint(source, 'helper', archive)
            with mock.patch.object(mobility_runtime.os, 'chown'), mock.patch.object(mobility_runtime.os, 'lchown'), \
                 mock.patch.object(mobility_runtime, 'directory', side_effect=lambda path, **_: path.mkdir(exist_ok=True)):
                mobility_runtime.restore(archive, state, 'helper', 'fixture')
                for name in paths[:6]:
                    self.assertEqual((state / name).read_text(), name)
                for name in paths[6:]:
                    self.assertFalse((state / name).exists())
                with tarfile.open(archive, 'w:gz') as malicious:
                    member = tarfile.TarInfo('work/../../outside')
                    member.size = 1
                    malicious.addfile(member, io.BytesIO(b'x'))
                with self.assertRaisesRegex(ValueError, 'outside'):
                    mobility_runtime.restore(archive, state, 'helper', 'fixture')

    def test_host_credentials_cannot_access_another_hosts_checkpoint(self):
        with TemporaryDirectory() as tmp:
            path = root(Path(tmp))
            identifier = 'f' * 32
            put(path / 'jobs' / 'job.json', {'host': 'alpha', 'transfer': identifier, 'operation': 'checkpoint'})
            self.assertEqual(archive_grant(path, 'alpha', identifier, 'PUT').name, identifier + '.tar.gz')
            for host, method in [('beta', 'PUT'), ('alpha', 'GET')]:
                with self.assertRaises(PermissionError):
                    archive_grant(path, host, identifier, method)

    def test_tls_channel_enrollment_transfer_and_device_authentication(self):
        with TemporaryDirectory() as tmp:
            data = Path(tmp)
            path = root(data)
            put(data / 'agents.json', {'agents': {}})
            put(path / 'endpoint.json', {'ip': '127.0.0.1', 'port': 0})
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                            '-subj', '/CN=fixture', '-addext', 'subjectAltName=IP:127.0.0.1',
                            '-keyout', str(path / 'server.key'), '-out', str(path / 'server.pem')],
                           capture_output=True, check=True)
            invitation, worker_token = 'invite-fixture', '4' * 64
            put(path / 'invites' / (secret_digest(invitation) + '.json'),
                {'host': 'worker.example.ts.net', 'node': 'node-1', 'expires': time.time() + 60})
            ready = Event()
            servers = []

            class FixtureServer(ThreadingHTTPServer):
                def serve_forever(self, *args, **kwargs):
                    servers.append(self)
                    ready.set()
                    return super().serve_forever(*args, **kwargs)

            with mock.patch('mobility_transport.ThreadingHTTPServer', FixtureServer), \
                 mock.patch('mobility_transport.signed_peer', return_value='node-1') as peer:
                thread = Thread(target=serve, args=(data,), daemon=True)
                thread.start()
                self.assertTrue(ready.wait(5))
                server = servers[0]
                config = {'endpoint': f'https://127.0.0.1:{server.server_port}',
                          'ca': (path / 'server.pem').read_text()}
                enroll = Channel({**config, 'credential': invitation})
                channel = Channel({**config, 'credential': worker_token})
                try:
                    response = enroll.json('/enroll', {'credential': worker_token})
                    self.assertEqual(response['host'], 'worker.example.ts.net')
                    self.assertEqual(enroll.json('/enroll', {'credential': worker_token}), response)
                    peer.return_value = 'other-node'
                    with self.assertRaises(HTTPError) as denied:
                        channel.json('/poll', {})
                    self.assertEqual(denied.exception.code, 403)
                    peer.return_value = 'node-1'
                    identifier = 'd' * 32
                    document = path / 'jobs/job.json'
                    operation = {'id': identifier, 'host': response['host'], 'transfer': identifier}
                    put(document, {**operation, 'operation': 'checkpoint'})
                    self.assertEqual(channel.json('/poll', {})['jobs'][0]['id'], identifier)
                    expected = b'synthetic private agent state'
                    checkpoint = data / 'source.tgz'
                    checkpoint.write_bytes(expected)
                    self.assertTrue(channel.upload(identifier, checkpoint)['ok'])
                    channel.json('/poll', {'completed': {identifier: {'ok': True}}})
                    self.assertTrue(mobility.load(document)['result']['ok'])
                    put(document, {**operation, 'operation': 'restore'})
                    downloaded = data / 'destination.tgz'
                    channel.download(identifier, downloaded)
                    self.assertEqual(downloaded.read_bytes(), expected)
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
