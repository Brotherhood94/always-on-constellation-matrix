import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy/matrix'))
from agent_catalog import modify
from connection_history import commit_registry, publish_connections


def roster(contacts, *, version=2, retiring=False):
    agents = {
        key: {'name': key.title(), 'profile': {'role': 'worker', 'contacts': allowed}}
        for key, allowed in [('coach', contacts), ('scouter', [])]
    }
    if retiring:
        agents['scouter']['retirement'] = 'draining'
    return {'team_version': version, 'dynamic_handoffs': True, 'agents': agents}


class ConnectionHistoryTests(unittest.TestCase):
    def test_upgrade_baselines_the_previous_roster_and_tracks_coaching_and_undo(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'agents.json'
            path.write_text(json.dumps(roster(['scouter'])))
            changed = roster([])
            commit_registry(path, changed, actor='coach', reason='coaching', evidence='$support')
            commit_registry(path, changed, actor='coach', reason='coaching', evidence='$support')
            commit_registry(path, roster(['scouter']), actor='coach', reason='coaching undo')
            history = json.loads(path.read_text())['connection_history']['pending']
            self.assertEqual([entry['sequence'] for entry in history], [1, 2, 3])
            self.assertEqual([entry['kind'] for entry in history], ['baseline', 'change', 'change'])
            self.assertEqual(history[0]['added'], [['coach', 'scouter']])
            self.assertEqual(history[1]['removed'], [['coach', 'scouter']])
            self.assertEqual(history[1]['evidence'], '$support')
            self.assertEqual(history[2]['added'], [['coach', 'scouter']])

    def test_delivery_retries_with_the_same_revision_after_a_lost_response(self):
        with TemporaryDirectory() as temp:
            data = Path(temp)
            (data / 'dashboard').mkdir()
            (data / 'rooms.json').write_text(json.dumps({'office': '!office:test.example'}))
            path = data / 'agents.json'
            commit_registry(path, roster(['scouter']))
            commit_registry(path, roster(['scouter'], retiring=True), reason='retirement')
            records = {}

            def graph(endpoint, payload):
                if endpoint == '/office/topology/revision':
                    revision = payload['revision']
                    records.setdefault(revision['id'], revision)
                    if failing and revision['sequence'] == 2:
                        raise OSError('Response lost')
                    return {'id': revision['id']}
                return {'coverage': 'fixture', 'changes': list(records.values()), 'handoffs': []}

            failing = True
            with self.assertRaises(OSError):
                publish_connections(data, graph)
            self.assertFalse((data / 'dashboard/connections.json').exists())
            failing = False
            publish_connections(data, graph)
            self.assertEqual(len(records), 2)
            snapshot = json.loads((data / 'dashboard/connections.json').read_text())
            self.assertEqual(snapshot['changes'][1]['removed'], [['coach', 'scouter']])

    def test_legacy_catalogs_do_not_invent_permitted_routes(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'agents.json'
            commit_registry(path, roster(['scouter'], version=1))
            baseline = json.loads(path.read_text())['connection_history']['pending'][0]
            self.assertFalse(baseline['enforced'])
            self.assertEqual(baseline['added'], [])

    def test_operator_profile_edit_records_only_the_effective_directed_change(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'agents.json'
            commit_registry(path, roster(['scouter']))

            def edit(registry):
                registry['agents']['scouter']['profile']['contacts'] = ['coach']

            modify(edit, path)
            modify(edit, path)
            records = json.loads(path.read_text())['connection_history']['pending']
            self.assertEqual(len(records), 2)
            self.assertEqual(records[1]['added'], [['scouter', 'coach']])
            self.assertEqual(records[1]['removed'], [])
            self.assertEqual(records[1]['reason'], 'operator profile update')
