"""Exercise the actual room provisioning API against a disposable Synapse."""
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from time import sleep
import unittest
from unittest.mock import patch

from installer import save
from layout import CLIENT, create_layout, direct_room, invite, join, link_child, login, encoded, room_route
from onboarding import finish
from operator_cli import matrix_session
from provision import MatrixError, request
from roster import deactivate_worker, invitation_ready, office_inviter, prepare_management
from roster_broker import verify
from telegram_provision import reconcile_layout
from vps_migration import revoke_legacy


class LayoutTest(unittest.TestCase):
    def test_operator_session_uses_local_matrix_login_and_logs_out(self):
        server = os.environ['MATRIX_SERVER_NAME']
        password = 'synthetic-test-passphrase-88'
        owner = login('operator', password)
        current = {'matrix_port': int(os.environ['MATRIX_LOCAL_PORT']),
                   'settings': {'owner': 'operator', 'server_name': server}}
        with matrix_session(current, password) as token:
            self.assertNotEqual(token, owner['access_token'])
            self.assertEqual(request('GET', CLIENT + '/account/whoami', token=token)['user_id'], owner['user_id'])
        with self.assertRaises(MatrixError) as expired:
            request('GET', CLIENT + '/account/whoami', token=token)
        self.assertEqual(expired.exception.code, 'M_UNKNOWN_TOKEN')

    def test_telegram_layout_migrates_only_known_links_and_reuses_private_rooms(self):
        domain = os.environ['MATRIX_SERVER_NAME']
        password = 'synthetic-test-passphrase-88'
        owner, bot = (login(account, password) for account in ('operator', 'telegrambot'))
        token = owner['access_token']
        rooms = create_layout(domain, owner['user_id'], token)
        agents = rooms['space']
        unrelated = request('POST', CLIENT + '/createRoom', {'preset': 'private_chat', 'invite': [owner['user_id']]},
                            bot['access_token'])['room_id']

        def children(room):
            return {event['state_key'] for event in request('GET', room_route(room) + '/state', token=token)
                    if event['type'] == 'm.space.child' and event['content'].get('via')}

        with TemporaryDirectory() as working:
            root = Path(working)
            (root / 'connector').mkdir()
            (root / 'guide').mkdir()
            save(root / 'matrix-password', password + '\n')
            args = (root, domain, owner['user_id'], token, agents)
            fresh = reconcile_layout(*args, bridge_active=False, select=lambda _: self.fail('No bridge DM'))
            self.assertFalse((root / 'database.json').exists())
            self.assertFalse((root / 'connector/config.json').exists())
            space, control = fresh['space'], fresh['control']
            joined = request('GET', CLIENT + '/joined_rooms', token=token)['joined_rooms']
            self.assertTrue({space, control} <= set(joined), 'The operator must join both Telegram rooms during setup')
            self.assertNotIn(unrelated, joined)
            self.assertEqual(children(space), {control})
            self.assertNotIn(space, children(agents))

            def direct(key):
                return direct_room(domain, agents, key, 'Fixture bridge chat', owner['user_id'], token,
                                   bot['user_id'], bot['access_token'])

            bridge = direct('legacy-telegram-bridge')
            other = direct('other-telegram-bridge')
            portal = request('POST', CLIENT + '/createRoom', {'preset': 'private_chat'}, token)['room_id']
            with sqlite3.connect(root / 'connector/telegram.sqlite3') as connection:
                connection.execute('CREATE TABLE binding (id TEXT, room TEXT)')
                connection.execute('INSERT INTO binding VALUES (?, ?)', ('test', portal))
            save(root / 'connector/config.json', {'space': space, 'controlRoom': control})
            for room in (space, control, portal):
                link_child(agents, room, domain, token)
            guide_token = (root / 'guide/token').read_text().strip()
            request('POST', room_route(control) + '/leave', {}, token)
            invite(control, owner['user_id'], guide_token)
            event = request('PUT', room_route(bridge) + '/send/m.room.message/layout-history',
                            {'msgtype': 'm.text', 'body': 'Fixture history'}, token)['event_id']
            selected = []

            def select(candidates):
                selected.append(set(candidates))
                return bridge

            migrated = reconcile_layout(*args, bridge_active=True, select=select)
            self.assertEqual(migrated, {**fresh, 'bridge': bridge})
            alias = CLIENT + '/directory/room/' + encoded(f'#telegram-control:{domain}')
            request('DELETE', alias, token=guide_token)
            self.assertEqual(reconcile_layout(*args, bridge_active=True, select=select), migrated)
            self.assertEqual(request('GET', alias, token=token)['room_id'], control)
            self.assertEqual(selected, [{bridge, other}])
            self.assertEqual(children(space), {control, bridge, portal})
            self.assertFalse({space, control, bridge, portal} & children(agents))
            self.assertTrue({rooms['office'], rooms['alerts'], other} <= children(agents))
            self.assertEqual(request('GET', room_route(bridge) + '/event/' + encoded(event), token=token)
                             ['content']['body'], 'Fixture history')
            self.assertEqual(set(request('GET', room_route(control) + '/joined_members', token=token)['joined']),
                             {owner['user_id'], f'@telegram-agent:{domain}'})

    def test_idempotent_space_direct_chats_and_pin_power(self):
        server = os.environ['MATRIX_SERVER_NAME']
        password = 'synthetic-test-passphrase-88'
        owner = login('operator', password)
        monitor = login('monitor', password)
        chief = login('chief-of-staff', password)
        dot = login('dot', password)
        analyst = login('analyst', password)
        manager = login('manager', password)
        token = owner['access_token']
        rooms = create_layout(server, owner['user_id'], token)
        self.assertEqual(create_layout(server, owner['user_id'], token), rooms)
        join(rooms['alerts'], monitor['user_id'], monitor['access_token'])
        for account in (dot, analyst, manager):
            invite(rooms['office'], account['user_id'], token)
            join(rooms['office'], account['user_id'], account['access_token'])
        request('PUT', room_route(rooms['alerts']) + '/state/m.room.pinned_events', {'pinned': []}, monitor['access_token'])
        invite(rooms['office'], chief['user_id'], token)
        join(rooms['office'], chief['user_id'], chief['access_token'])
        dm = direct_room(server, rooms['space'], 'chief-of-staff', 'Chief of Staff', owner['user_id'], token,
                         chief['user_id'], chief['access_token'])
        self.assertEqual(dm, direct_room(server, rooms['space'], 'chief-of-staff', 'Chief of Staff', owner['user_id'],
                                         token, chief['user_id'], chief['access_token']))
        members = request('GET', room_route(dm) + '/joined_members', token=token)['joined']
        self.assertEqual(set(members), {owner['user_id'], chief['user_id']})
        for perspective, peer in [(owner, chief), (chief, owner)]:
            data = request('GET', f"{CLIENT}/user/{encoded(perspective['user_id'])}/account_data/m.direct",
                           token=perspective['access_token'])
            self.assertEqual(data[peer['user_id']], [dm])
        state = request('GET', room_route(rooms['space']) + '/state', token=token)
        children = {event['state_key'] for event in state if event['type'] == 'm.space.child'}
        self.assertEqual(children, {rooms['office'], rooms['alerts'], dm})

    def test_welcome_waits_for_remote_confirmation_and_real_office_membership(self):
        server = os.environ['MATRIX_SERVER_NAME']
        password = 'synthetic-test-passphrase-88'
        owner, manager, remote = (login(account, password) for account in ('operator', 'manager', 'remote-worker'))
        owner_token = owner['access_token']
        rooms = create_layout(server, owner['user_id'], owner_token)
        office = rooms['office']
        invite(office, manager['user_id'], owner_token)
        join(office, manager['user_id'], manager['access_token'])
        settings = {'server_name': server, 'owner': 'operator', 'agents': ['manager', 'remote-worker'],
                    'dot': False, 'telegram': False, 'tailscale': False, 'dashboard_url': 'https://fixture.test:8443',
                    'context_url': 'https://fixture.test:8444/browser/'}
        worker = {'name': 'Remote Worker', 'profile': {'role': 'worker'}, 'active': False}
        registry = {'agents': {'manager': {'name': 'Manager', 'profile': {'role': 'coordinator'}, 'active': True},
                               'remote-worker': worker}}
        with TemporaryDirectory() as working, patch('onboarding.open_existing', return_value=(settings, registry)), \
             patch('onboarding.choice', return_value='manager'), patch('onboarding.verified_pins', return_value={'ready': True}), \
             patch('onboarding.session_token', return_value='fixture'), patch('onboarding.publish', return_value='$welcome') as publish:
            data = Path(working)
            matrix = data / 'state/manager/matrix'
            matrix.mkdir(parents=True)
            save(matrix / 'matrix-token', manager['access_token'])
            save(data / 'rooms.json', rooms)
            save(data / 'onboarding.json', {'confirmed': ['element', 'dashboard', 'context']})
            self.assertIn('Waiting for agent setup in Element: Remote Worker', finish(data)['waiting'])
            worker['active'] = True
            invite(office, remote['user_id'], owner_token)
            self.assertIn('Waiting for these agents to join Office in Element: Remote Worker', finish(data)['waiting'])
            publish.assert_not_called()
            join(office, remote['user_id'], remote['access_token'])
            self.assertEqual(finish(data), {'sender': manager['user_id'], 'room': office, 'event': '$welcome'})
            publish.assert_called_once()

    def test_vps_manager_replaces_root_in_the_same_private_chat(self):
        server = os.environ['MATRIX_SERVER_NAME']
        password = 'synthetic-test-passphrase-88'
        owner, old, replacement = (login(key, password) for key in ('operator', 'root', 'vps-manager'))
        token = owner['access_token']
        rooms = create_layout(server, owner['user_id'], token)
        invite(rooms['office'], old['user_id'], token)
        join(rooms['office'], old['user_id'], old['access_token'])
        audit = direct_room(server, rooms['space'], 'root', 'Root Operations', owner['user_id'], token,
                            old['user_id'], old['access_token'])
        original = request('PUT', room_route(audit) + '/send/m.room.encrypted/old-root-receipt', {
            'algorithm': 'm.megolm.v1.aes-sha2', 'ciphertext': 'synthetic-envelope',
        }, old['access_token'])['event_id']
        with TemporaryDirectory() as working, patch('vps_migration.disconnect') as disconnect:
            data = Path(working)
            matrix = data / 'state/root/matrix'
            matrix.mkdir(parents=True)
            save(matrix / 'room-id', audit)
            save(matrix / 'matrix-password', password)
            settings = {'server_name': server}
            revoke_legacy(data, settings, rooms, token)
            revoke_legacy(data, settings, rooms, token)
            disconnect.assert_called_with(data, 'root')
        with self.assertRaises(MatrixError):
            request('GET', CLIENT + '/account/whoami', token=old['access_token'])
        self.assertNotIn(old['user_id'], request('GET', room_route(rooms['office']) + '/joined_members', token=token)['joined'])
        self.assertEqual(audit, direct_room(server, rooms['space'], 'root', 'Root Operations', owner['user_id'],
                                           token, replacement['user_id'], replacement['access_token']))
        self.assertEqual(set(request('GET', room_route(audit) + '/joined_members', token=token)['joined']),
                         {owner['user_id'], replacement['user_id']})
        mapping = request('GET', f"{CLIENT}/user/{encoded(owner['user_id'])}/account_data/m.direct", token=token)
        self.assertNotIn(old['user_id'], mapping)
        self.assertEqual(mapping[replacement['user_id']], [audit])
        previous = request('GET', room_route(audit) + '/event/' + encoded(original), token=token)
        self.assertEqual((previous['sender'], previous['type']), (old['user_id'], 'm.room.encrypted'))

    def test_roster_invitation_approval_receipt_and_retirement_against_synapse(self):
        server = os.environ['MATRIX_SERVER_NAME']
        password = 'synthetic-test-passphrase-88'
        owner, chief, worker = (login(key, password) for key in ('operator', 'chief-of-staff', 'roster-fixture'))
        owner_token, chief_token, worker_token = (account['access_token'] for account in (owner, chief, worker))
        rooms = create_layout(server, owner['user_id'], owner_token)
        dm = direct_room(server, rooms['space'], 'chief-of-staff', 'Chief of Staff', owner['user_id'],
                         owner_token, chief['user_id'], chief_token)
        settings = {'server_name': server, 'owner': 'operator', 'systemd': True}
        with TemporaryDirectory() as work:
            data = Path(work)
            save(data / 'rooms.json', rooms)
            self.assertFalse(invitation_ready(data))
            prepare_management(data, settings, {'agents': {'chief-of-staff': {'active': False}}}, owner_token)
            self.assertTrue(invitation_ready(data))
            inviter = office_inviter(data, settings, owner_token)
            self.assertEqual(office_inviter(data, settings, owner_token), inviter)
            office = rooms['office']
            invite(office, worker['user_id'], inviter)
            join(office, worker['user_id'], worker_token)
            with self.assertRaises(MatrixError):
                request('PUT', room_route(office) + '/state/m.room.name', {'name': 'Changed'}, inviter)
            with self.assertRaises(MatrixError):
                invite(dm, worker['user_id'], inviter)

            def encrypted(room, step, token, relation=None):
                content = {'algorithm': 'm.megolm.v1.aes-sha2', 'ciphertext': 'synthetic-envelope'}
                if relation:
                    content['m.relates_to'] = {'rel_type': 'm.reference', 'event_id': relation}
                return request('PUT', room_route(room) + '/send/m.room.encrypted/roster-' + step, content, token)['event_id']

            matrix = data / 'state/chief-of-staff/matrix'
            matrix.mkdir(parents=True)
            save(matrix / 'room-id', dm)
            save(matrix / 'matrix-token', chief_token)
            proposal = {'request': encrypted(dm, 'request', owner_token),
                        'proposal': encrypted(dm, 'proposal', chief_token),
                        'poll': encrypted(dm, 'poll', chief_token)}
            proposal['vote'] = encrypted(dm, 'vote', owner_token, proposal['poll'])
            vote = request('GET', room_route(dm) + '/event/' + encoded(proposal['vote']), token=chief_token)
            proposal['decided'] = vote['origin_server_ts']
            verify(data, settings, proposal, proposal['decided'])

            history = encrypted(office, 'history', worker_token)
            matrix = data / 'state/roster-fixture/matrix'
            matrix.mkdir(parents=True)
            save(matrix / 'matrix-password', password)
            deactivate_worker(data, 'roster-fixture', settings)
            deactivate_worker(data, 'roster-fixture', settings)
            with self.assertRaises(MatrixError):
                request('GET', CLIENT + '/account/whoami', token=worker_token)
            for _ in range(30):
                if worker['user_id'] not in request('GET', room_route(office) + '/joined_members', token=owner_token)['joined']:
                    break
                sleep(0.2)
            else:
                self.fail('The retired worker stayed in Office')
            stored = request('GET', room_route(office) + '/event/' + encoded(history), token=owner_token)
            self.assertEqual(stored['sender'], worker['user_id'])
            self.assertEqual(stored['type'], 'm.room.encrypted')


if __name__ == '__main__':
    unittest.main()
