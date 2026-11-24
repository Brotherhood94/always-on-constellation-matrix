"""Exercise the actual room provisioning API against a disposable Synapse."""
import os
import unittest

from layout import CLIENT, create_layout, direct_room, invite, join, login, encoded, room_route
from provision import request


class LayoutTest(unittest.TestCase):
    def test_idempotent_space_direct_chats_and_pin_power(self):
        server = os.environ['MATRIX_SERVER_NAME']
        password = 'synthetic-test-passphrase-88'
        owner = login('operator', password)
        monitor = login('monitor', password)
        chief = login('chief-of-staff', password)
        dot = login('dot', password)
        analyst = login('analyst', password)
        token = owner['access_token']
        rooms = create_layout(server, owner['user_id'], token)
        self.assertEqual(create_layout(server, owner['user_id'], token), rooms)
        join(rooms['alerts'], monitor['user_id'], monitor['access_token'])
        for account in (dot, analyst):
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


if __name__ == '__main__':
    unittest.main()
