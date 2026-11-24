import sys
from pathlib import Path
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from persona_management import enroll, TARGETS

ID = 'c7d6d224-eec4-4dab-8e7d-ce1c04c54495'
ORIGIN = 'https://engine-fixture:19443'


class PersonaManagementTest(TestCase):
    def test_sync_creates_one_native_target_then_reads_it_without_minting_a_token(self):
        calls = []
        items = []
        target = {'id': ID, 'url': ORIGIN, 'agent_id': 'instance-1', 'name': 'Fixture'}
        expected = {'target': target, 'installation': {'revision': 2, 'active': None}}

        def request(method, path, payload=None):
            calls.append((method, path))
            if method == 'POST':
                self.assertEqual(payload['url'], ORIGIN)
                self.assertEqual(payload['token'], 'profile-token-fixture')
                items.append(target)
                return expected
            if path == TARGETS:
                return {'items': items}
            return expected

        self.assertEqual(enroll(request, 'fixture', ORIGIN, lambda: {'token': 'profile-token-fixture'}), expected)
        self.assertEqual(enroll(request, 'fixture', ORIGIN, lambda: self.fail('Issued a new token'), 'instance-1'), expected)
        self.assertEqual(calls, [('GET', TARGETS), ('POST', TARGETS), ('GET', TARGETS), ('GET', f'{TARGETS}/{ID}')])

    def test_existing_identity_cannot_be_replaced_silently(self):
        def request(method, path):
            self.assertEqual((method, path), ('GET', TARGETS))
            return {'items': [{'id': ID, 'url': ORIGIN, 'agent_id': 'different-instance'}]}

        with self.assertRaisesRegex(ValueError, 'different Instance'):
            enroll(request, 'fixture', ORIGIN, lambda: self.fail('read credentials'), 'instance-1')
