from contextlib import ExitStack, redirect_stdout
from io import StringIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from installer import load, save
from vps_migration import migrate_root


class VPSMigrationTest(TestCase):
    def test_context_failure_resumes_after_revocation_and_keeps_existing_agents(self):
        with TemporaryDirectory() as work, ExitStack() as stack, redirect_stdout(StringIO()):
            data = Path(work)
            save(data / 'settings.json', {'server_name': 'fixture.test', 'owner': 'operator',
                                          'agents': ['root', 'manager'], 'systemd': True,
                                          'subscriptions': {'overrides': {'root': 'fixture'}}})
            manager = {'name': 'Manager', 'placement': 'local', 'active': True}
            save(data / 'agents.json', {'agents': {'root': {'name': 'Root'}, 'manager': manager}})
            save(data / 'rooms.json', {'office': '!office:fixture.test'})
            effects = {name: stack.enter_context(patch('vps_migration.' + name)) for name in (
                'open_existing', 'password', 'login', 'stop_legacy', 'revoke_legacy', 'archive_legacy', 'compose',
                'compose_config', 'refresh_context', 'prepare_agent_state', 'prepare_images',
                'sync_workshop', 'publish_inventory', 'up', 'request')}
            effects['login'].return_value = {'access_token': 'synthetic-operator-token'}
            effects['open_existing'].side_effect = lambda path: (load(path / 'settings.json'), load(path / 'agents.json'))
            effects['refresh_context'].side_effect = [OSError('synthetic Context failure'), None]
            with self.assertRaisesRegex(OSError, 'synthetic Context failure'):
                migrate_root(data, None, True)
            self.assertEqual(load(data / 'the-architect-migration.json'), {'phase': 'revoked'})
            effects['prepare_agent_state'].assert_not_called()
            migrate_root(data, None, False)
            migrate_root(data, None, False)
            effects['stop_legacy'].assert_called_once()
            effects['revoke_legacy'].assert_called_once()
            effects['prepare_agent_state'].assert_called_once()
            self.assertEqual(load(data / 'the-architect-migration.json'), {'phase': 'complete'})
            roster = load(data / 'agents.json')
            self.assertEqual(set(roster['agents']), {'the-architect', 'manager'})
            self.assertEqual(roster['agents']['manager'], manager)
            self.assertFalse(roster['agents']['the-architect']['active'])
            self.assertEqual(roster['agents']['the-architect']['subscription'], 'fixture')
            self.assertIn('root', roster['retired'])
            self.assertEqual(load(data / 'settings.json')['agents'], ['the-architect', 'manager'])
