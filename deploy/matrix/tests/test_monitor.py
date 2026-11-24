import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from monitor import SCHEMA, agent_inventory, record


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


if __name__ == "__main__":
    unittest.main()
