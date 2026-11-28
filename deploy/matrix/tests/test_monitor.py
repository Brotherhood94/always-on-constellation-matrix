from datetime import datetime, timezone
import json
import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from monitor import SCHEMA, agent_inventory, export, integration_status, record
from telegram_health import telegram_status


class AlarmTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.executescript(SCHEMA)

    def send_samples(self, resource, start, values):
        with self.db:
            for i, value in enumerate(values):
                record(self.db, resource, (start + i) * 60, value)

    def messages(self):
        return [row[0] for row in self.db.execute("SELECT body FROM outbox ORDER BY time")]

    def test_cpu_threshold_streak_hysteresis_and_recovery(self):
        self.send_samples("CPU", 0, [51, 51, 51, 51, 50, 60, 60, 60, 60])
        self.assertEqual(self.messages(), [])
        self.send_samples("CPU", 9, [60, 80, 45, 44, 44, 44, 44, 45])
        self.assertEqual(len(self.messages()), 1)
        self.assertIn("CPU usage above threshold: 60.0%", self.messages()[0])
        self.send_samples("CPU", 17, [44, 44, 44, 44, 44, 44])
        self.assertEqual(len(self.messages()), 2)
        self.assertIn("CPU usage recovered: 44.0%", self.messages()[1])

    def test_disk_only_crosses_strict_boundaries(self):
        self.send_samples("Disk", 0, [25, 26, 27, 22.5, 22])
        self.assertEqual(len(self.messages()), 2)
        self.assertIn("threshold 25%", self.messages()[0])
        self.assertIn("threshold 22.5%", self.messages()[1])

    def test_duplicate_sample_does_not_advance_counter(self):
        self.send_samples("RAM", 0, [70])
        for _ in range(6):
            self.send_samples("RAM", 0, [70])
        self.assertEqual(self.messages(), [])
        self.send_samples("RAM", 1, [70, 70, 70, 70])
        self.assertEqual(len(self.messages()), 1)

    def test_reverted_sample_leaves_no_queued_alert(self):
        self.db.execute("BEGIN")
        record(self.db, "Disk", 1, 26)
        self.db.rollback()
        self.assertEqual(self.messages(), [])
        self.assertIsNone(self.db.execute("SELECT active FROM alarms").fetchone())


class AgentInventoryTests(unittest.TestCase):
    def test_live_compose_settings_and_readiness(self):
        def container(user, health, model, effort):
            return {
                "Config": {"Labels": {"com.docker.compose.service": user}, "Env": [
                    f"AGENT_USERNAME={user}", f"AGENT_NAME={user.title()}",
                    f"AGENT_MODEL={model}", f"AGENT_REASONING={effort}", "PRIVATE_TOKEN=should-not-be-published",
                ]},
                "State": {"Running": True, "Health": {"Status": health}},
            }

        with TemporaryDirectory() as directory:
            root = Path(directory)
            analyst = container("analyst", "healthy", "gpt-6.2-sol", "medium")
            reviewer = container("reviewer", "unhealthy", "gpt-6.2-sol", "high")
            one_off = container("reviewer", "healthy", "probe", "low")
            one_off["Config"]["Labels"]["com.docker.compose.oneoff"] = "True"
            self.assertEqual(agent_inventory([one_off, reviewer, analyst], root), [
                {"name": "Analyst", "model": "gpt-6.2-sol", "effort": "medium", "status": "Available"},
                {"name": "Reviewer", "model": "gpt-6.2-sol", "effort": "high", "status": "Unavailable"},
            ])
            analyst["Config"]["Env"][-2] = "AGENT_REASONING=high"
            self.assertEqual(agent_inventory([analyst], root)[0]["effort"], "high")

    def test_telegram_telemetry_refreshes_before_the_catalog_and_expires_on_failure(self):
        now = 1_800_000_000
        services = {'connector': {'state': 'running', 'health': None},
                    'telegram-engine': {'state': 'running', 'health': 'healthy'},
                    'telegram-bridge': {'state': 'running', 'health': 'healthy'}}
        registry = {'local_server': 'fixture.test', 'agents': {}}
        with TemporaryDirectory() as directory:
            data = Path(directory)
            dashboard = data / 'dashboard'
            connector = data / 'telegram/connector'
            dashboard.mkdir()
            connector.mkdir(parents=True)
            (data / 'settings.json').write_text('{"telegram":true}')
            with sqlite3.connect(connector / 'telegram.sqlite3') as telegram, sqlite3.connect(':memory:') as db:
                db.executescript(SCHEMA)
                telegram.executescript('''
                    CREATE TABLE draft (state, body);
                    INSERT INTO draft VALUES ('awaiting_approval', 'this message must stay private');
                    CREATE TABLE work (queued, state);
                    CREATE TABLE settings (key, value);
                ''')
                telegram.execute('INSERT INTO settings VALUES (?, ?)', ('last_sync', datetime.fromtimestamp(now, timezone.utc).isoformat()))
                telegram.execute("INSERT INTO settings VALUES ('paused','false')")
                telegram.commit()

                def publish(at):
                    export(db, data / 'outbox', data / 'acks', dashboard, at, data)
                    agent = json.loads((dashboard / 'agents.json').read_text())['agents'][0]
                    integration = json.loads((dashboard / 'integrations.json').read_text())['services']['Telegram Agent']
                    self.assertEqual(agent['status'], integration)
                    return agent['status']

                with patch.dict('monitor.os.environ', {'MATRIX_SERVER_NAME': 'fixture.test'}), \
                        patch('monitor.read_agent_registry', return_value=registry), patch('monitor.collect_agents', return_value=[]), \
                        patch('monitor.workshop_profiles', return_value={}), patch('monitor.refresh_connections'), \
                        patch('monitor.integration_status', return_value=({'Telegram Agent': 'running'}, services)):
                    self.assertEqual(publish(now), 'Available')
                    telegram.execute("UPDATE settings SET value='true' WHERE key='paused'")
                    telegram.commit()
                    self.assertEqual(publish(now + 60), 'Paused')
                    report = (dashboard / 'telegram.json').read_text()
                    self.assertEqual(json.loads(report)['pendingDrafts'], 1)
                    self.assertNotIn('this message must stay private', report)
                    with patch('monitor.publish_telegram_health', side_effect=sqlite3.OperationalError('busy')):
                        self.assertEqual(publish(now + 241), 'Status stale')
                    self.assertEqual((dashboard / 'telegram.json').read_text(), report)

    def test_telegram_observations_ignore_one_off_containers(self):
        def container(service, project='fixture-telegram', one_off='False'):
            labels = {'com.docker.compose.service': service, 'com.docker.compose.project': project,
                      'com.docker.compose.oneoff': one_off}
            return {'Config': {'Labels': labels}, 'State': {'Status': 'running'}}

        rows = [container('connector'), container('telegram-engine', one_off='True'), container('context', 'fixture')]
        with patch.dict('monitor.os.environ', {'MATRIX_PROJECT': 'fixture', 'TELEGRAM_PROJECT': 'fixture-telegram'}), \
                patch('monitor.project_containers', return_value=['fixture']), patch('monitor.command', return_value=json.dumps(rows)):
            services, telegram = integration_status()
        self.assertEqual(services, {'Telegram Agent': 'running', 'Context Intelligence': 'running'})
        self.assertEqual(telegram, {'connector': {'state': 'running', 'health': None}})

    def test_telegram_requires_the_bridge_and_a_recent_sync_even_with_a_remote_engine(self):
        now = 1_800_000_000
        stamp = datetime.fromtimestamp(now, timezone.utc).isoformat()
        report = {'generatedAt': stamp, 'lastSync': stamp, 'services': {'connector': {'state': 'running'}}}
        self.assertEqual(telegram_status(report, now, remote_engine=True)[0], 'Setup required')
        report['services']['telegram-bridge'] = {'state': 'running', 'health': 'unhealthy'}
        self.assertEqual(telegram_status(report, now, remote_engine=True)[0], 'Unavailable')
        report['services']['telegram-bridge']['health'] = 'healthy'
        self.assertEqual(telegram_status(report, now, remote_engine=True)[0], 'Available')
        report['lastSync'] = None
        self.assertEqual(telegram_status(report, now, remote_engine=True)[0], 'Status stale')


if __name__ == "__main__":
    unittest.main()
