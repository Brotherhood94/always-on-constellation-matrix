import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'deploy/matrix'))
from telegram_health import SERVICES, publish_telegram_health


raw = sys.stdin.read().strip()
containers = json.loads(raw) if raw.startswith('[') else [json.loads(line) for line in raw.splitlines()]
services = {row['Service']: {'state': row['State'], 'health': row.get('Health') or None}
            for row in containers if row['Service'] in SERVICES}
publish_telegram_health(Path(os.environ['MATRIX_DATA']), services, int(time.time()))
