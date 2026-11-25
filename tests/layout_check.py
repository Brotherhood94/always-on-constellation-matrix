"""Exercise the actual room provisioning API against a disposable Synapse."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep
import unittest
from unittest.mock import patch

from installer import save
from layout import CLIENT, create_layout, direct_room, invite, join, login, encoded, room_route
from onboarding import finish
from provision import MatrixError, request
from roster import deactivate_worker, invitation_ready, office_inviter, prepare_management
from roster_broker import verify


class LayoutTest(unittest.TestCase):
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
            self.assertIn('Waiting for agent setup in Element: Remote Worker', finish(data, 'fixture')['waiting'])
            worker['active'] = True
            invite(office, remote['user_id'], owner_token)
            self.assertIn('Waiting for these agents to join Office in Element: Remote Worker', finish(data, 'fixture')['waiting'])
            publish.assert_not_called()
            join(office, remote['user_id'], remote['access_token'])
            self.assertEqual(finish(data, 'fixture'), {'sender': manager['user_id'], 'room': office, 'event': '$welcome'})
            publish.assert_called_once()

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
