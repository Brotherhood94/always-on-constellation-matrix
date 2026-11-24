import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
import recovery


class RecoveryTest(TestCase):
    def test_services_start_again_if_checkpoint_fails(self):
        with TemporaryDirectory() as work:
            data = Path(work)
            (data / 'backups').mkdir()
            (data / 'backups/config').touch()
            (data / 'secrets').mkdir()
            (data / 'secrets/restic-password').write_text('fixture')
            calls = []

            def failed_backup(*args, **options):
                calls.append(args)
                if 'backup' in args:
                    raise subprocess.CalledProcessError(1, args)

            with patch.object(recovery, 'run', failed_backup), patch.object(recovery, 'services', return_value=['context']):
                with self.assertRaises(subprocess.CalledProcessError):
                    recovery.backup(data, 'backup')
            self.assertEqual(calls[0][-2:], ('stop', 'context'))
            self.assertEqual(calls[-1][-2:], ('start', 'context'))
            self.assertIn(str(data / 'backups'), calls[1])
