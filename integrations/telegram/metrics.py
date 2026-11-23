import json
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone

from bridge_config import write

state = Path(os.environ['TELEGRAM_DATA'])
connector = state / 'connector'
output = Path(os.environ['MATRIX_DATA']) / 'dashboard/telegram.json'
raw = sys.stdin.read()
if not raw.strip():
    containers = []
elif raw.lstrip().startswith('['):
    containers = json.loads(raw)
else:
    containers = [json.loads(line) for line in raw.splitlines() if line.strip()]
services = {row['Service']: {'state': row['State'], 'health': row.get('Health') or None} for row in containers}
report = {'generatedAt': datetime.now(timezone.utc).isoformat(), 'services': services, 'pendingDrafts': 0,
          'unconfirmedDeliveries': 0, 'oldestQueuedAt': None, 'lastSync': None, 'paused': (connector / 'paused').exists()}
database = connector / 'telegram.sqlite3'
if database.is_file():
    with sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True, timeout=2) as session:
        report['pendingDrafts'] = session.execute("SELECT COUNT(*) FROM draft WHERE state='awaiting_approval'").fetchone()[0]
        report['unconfirmedDeliveries'] = session.execute("SELECT COUNT(*) FROM draft WHERE state IN ('publishing','matrix_accepted','unknown')").fetchone()[0]
        oldest = session.execute("SELECT MIN(queued) FROM work WHERE state IN ('queued','running')").fetchone()[0]
        if oldest is not None:
            report['oldestQueuedAt'] = datetime.fromtimestamp(oldest / 1000, timezone.utc).isoformat()
        row = session.execute("SELECT value FROM settings WHERE key='last_sync'").fetchone()
        if row:
            report['lastSync'] = row[0]
        row = session.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        report['paused'] = report['paused'] or bool(row and row[0] == 'true')
engine = connector / 'config.json'
if engine.is_file():
    report['agent'] = {'name': 'Telegram', 'model': json.loads(engine.read_text()).get('model'),
                       'effort': 'low', 'connector': services.get('connector', {}).get('state', 'stopped')}
write(output, report, 0o644)
