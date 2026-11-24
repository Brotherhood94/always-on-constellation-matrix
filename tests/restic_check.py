"""Check the real Restic round trip on a disposable native Linux filesystem."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from installer import save
import recovery

with TemporaryDirectory(dir='/state', prefix='constellation-checkpoint-') as work:
    data = Path(work) / 'data'
    for name in ('backups', 'secrets', 'postgres', 'desktop'):
        (data / name).mkdir(parents=True, exist_ok=True)
    save(data / 'settings.json', {'project': 'fixture'})
    save(data / 'rooms.json', {'office': '!fixture'})
    save(data / 'postgres/PG_VERSION', '17\n')
    save(data / 'desktop/report.txt', 'Private fixture artifact\n')
    with patch.object(recovery, 'services', return_value=[]):
        recovery.backup(data, 'backup')
        recovery.backup(data, 'restore-check')
    assert (data / 'last-successful-backup').is_file()
